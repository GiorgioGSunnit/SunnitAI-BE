"""
Neo4J graph writer — writes the payload produced by build_neo4j_graph_payload()
to the database using MERGE (fully idempotent, safe to re-run).

Env vars:
    NEO4J_URI       bolt://host:7687  (required — if unset, writes are skipped)
    NEO4J_USER      neo4j             (default: neo4j)
    NEO4J_PASSWORD                    (required when NEO4J_URI is set)
    NEO4J_DATABASE  neo4j             (default: neo4j)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Labels that get a uniqueness constraint on their `id` property.
# Order matters: constraints must exist before MERGE is called.
_CONSTRAINED_LABELS = [
    # Core document graph (build_neo4j_graph_payload)
    "LEGAL_DOC",
    "LEGAL_SOURCE",
    "DOCUMENT_SECTION",
    "EDITOR",
    # Enrichment nodes (graph_enrichment.py)
    "LEGAL_CONCEPT",
    "LEGAL_ACTION",
    "DATE",
    "ORGANIZATION",
    "PERSON",
    "ROLE",
    "LOCATION",
    # Parse-only test schema (test_pipeline.py --skip-llm)
    "Document",
    "Articolo",
    "Comma",
]

# Max nodes/relationships sent in a single UNWIND batch.
_BATCH_SIZE = 500

# Labels that skip the normal MERGE path and do a pre-existence check first.
_PRECHECKED_LABELS = {"LEGAL_DOC"}

# Cypher identifiers must be word-characters only — reject anything else
# before it reaches an f-string query to prevent injection.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, context: str) -> None:
    """Raise ValueError if *name* is not a safe Cypher identifier."""
    if not _SAFE_IDENTIFIER.match(name):
        raise ValueError(
            f"graph_writer: unsafe {context} identifier rejected: {name!r}"
        )


def is_configured() -> bool:
    """Return True if NEO4J_URI is set in the environment."""
    return bool(os.environ.get("NEO4J_URI"))


def _get_driver():
    try:
        from neo4j import GraphDatabase
    except ImportError as e:
        raise ImportError(
            "neo4j package is not installed. Add 'neo4j' to requirements.txt."
        ) from e

    uri = os.environ.get("NEO4J_URI")
    if not uri:
        raise RuntimeError("NEO4J_URI is not set — cannot connect to Neo4J.")

    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, password))


def _ensure_constraints(session) -> None:
    """
    Create uniqueness constraints on the `id` property for all known labels.
    Uses IF NOT EXISTS so it is safe to call on every run.
    Constraints also create an implicit index — MERGE on `id` becomes O(log n).
    """
    for label in _CONSTRAINED_LABELS:
        try:
            session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )
        except Exception as exc:
            # Older Neo4J versions (< 4.4) use a different syntax — log and continue.
            logger.debug("Could not create constraint for %s: %s", label, exc)


def _clean_props(props: dict, truncate_vector_key: str = "vettore") -> dict:
    """Remove None values and truncate vector fields to 1536 dims."""
    cleaned = {k: v for k, v in props.items() if v is not None}
    for key in (truncate_vector_key, "vector", "embedding"):
        if key in cleaned and isinstance(cleaned[key], list):
            cleaned[key] = [float(x) for x in cleaned[key][:1536]]
    return cleaned


def _write_nodes_batched(session, nodes: list[dict]) -> int:
    """
    Write nodes in UNWIND batches — one round-trip per _BATCH_SIZE nodes
    per unique label combination.

    Groups nodes by their label string so the MERGE clause can use a fixed label.
    Returns total nodes written.
    """
    from collections import defaultdict

    # Group by label string (e.g. "LEGAL_DOC", "DOCUMENT_SECTION:ORGANIZATION")
    by_label: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        node_id = node.get("id")
        if not node_id:
            logger.warning("graph_writer: skipping node without id: %s", node)
            continue
        labels = node.get("labels") or ["Node"]
        # Validate every label part before it reaches an f-string query.
        try:
            for lbl in labels:
                _validate_identifier(lbl, "label")
        except ValueError as exc:
            logger.warning("graph_writer: skipping node %s — %s", node_id, exc)
            continue
        label_str = ":".join(labels)
        props = _clean_props(node.get("properties", {}))
        props["id"] = node_id  # ensure id is in the props map for SET +=
        by_label[label_str].append({"id": node_id, "props": props})

    # _PRECHECKED_LABELS: pre-existence check before MERGE to avoid constraint
    # violations when the node was already relabeled (e.g. LEGAL_DOC → Document).

    total = 0
    for label_str, items in by_label.items():
        for i in range(0, len(items), _BATCH_SIZE):
            batch = items[i : i + _BATCH_SIZE]

            if label_str in _PRECHECKED_LABELS:
                # Check which ids already exist under any label (e.g. relabeled to Document)
                ids = [row["id"] for row in batch]
                existing = session.run(
                    "MATCH (n) WHERE n.id IN $ids RETURN n.id AS id",
                    ids=ids,
                ).data()
                existing_ids = {r["id"] for r in existing}
                new_batch = [row for row in batch if row["id"] not in existing_ids]
                update_batch = [row for row in batch if row["id"] in existing_ids]
            else:
                new_batch = batch
                update_batch = []

            if new_batch:
                session.run(
                    f"""
                    UNWIND $batch AS row
                    MERGE (n:{label_str} {{id: row.id}})
                    SET n += row.props
                    """,
                    batch=new_batch,
                )

            if update_batch:
                session.run(
                    """
                    UNWIND $batch AS row
                    MATCH (n {id: row.id})
                    SET n += row.props
                    """,
                    batch=update_batch,
                )

            skipped = len(update_batch)
            logger.info(
                "graph_writer: wrote %d nodes with label %s, skipped %d existing",
                len(new_batch), label_str, skipped,
            )
            total += len(batch)

    return total


def _resolve_document_elementids(session, ids: set[str]) -> dict[str, str]:
    """
    Resolve the current elementId of each Document node, label-agnostic.

    A document's LEGAL_DOC node is relabeled to Document by post_process
    after its first ingestion, but the payload rebuilt for a re-ingestion
    still describes it as LEGAL_DOC. Matching those relationships by label
    would silently miss the node (now Document) or need a label the fresh
    document doesn't have yet (LEGAL_DOC). Resolving the elementId once,
    without a label, sidesteps that — every relationship touching the
    document is then matched by elementId instead of by label.
    """
    if not ids:
        return {}
    result = session.run(
        "MATCH (n) WHERE n.id IN $ids RETURN n.id AS id, elementId(n) AS eid",
        ids=list(ids),
    ).data()
    return {r["id"]: r["eid"] for r in result}


def _endpoint_match(var: str, id_field: str, descriptor: tuple[str, str]) -> tuple[str, str | None]:
    """
    Build the MATCH pattern (and optional WHERE fragment) for one
    relationship endpoint.

    descriptor is ("label", "Label1:Label2") to match by id within a
    label — letting the planner use that label's id unique index — or
    ("eid", element_id) to match a specific node regardless of its
    current label(s) (see _resolve_document_elementids). Returns
    (pattern, where_fragment_or_None); the where fragment references a
    query parameter named "{var}_eid" that the caller must bind.
    """
    kind, value = descriptor
    if kind == "label":
        for lbl in value.split(":"):
            _validate_identifier(lbl, "label")
        return f"({var}:{value} {{id: row.{id_field}}})", None
    return f"({var})", f"elementId({var}) = ${var}_eid"


def _write_relationships_labelled(
    session, relationships: list[dict], id_to_labels: dict[str, list[str]]
) -> tuple[int, list[dict]]:
    """
    Write relationships whose endpoints have a known label (from the
    payload's own nodes), grouped by (type, source label, target label) so
    MATCH can name a label and the planner can use that label's id unique
    index (NodeUniqueIndexSeek) instead of scanning every label with an id
    property.

    Returns (written_count, rows_with_an_unresolvable_endpoint) — the
    latter for the caller to retry with the unlabelled fallback.
    """
    from collections import defaultdict

    document_ids = {
        node_id for node_id, labels in id_to_labels.items() if "LEGAL_DOC" in labels
    }
    doc_eid = _resolve_document_elementids(session, document_ids)

    def _descriptor(node_id: str) -> tuple[str, str] | None:
        if node_id in doc_eid:
            return ("eid", doc_eid[node_id])
        labels = id_to_labels.get(node_id)
        if not labels:
            return None
        return ("label", ":".join(labels))

    by_group: dict[tuple, list[dict]] = defaultdict(list)
    unresolved: list[dict] = []
    for rel in relationships:
        rel_type = rel.get("type")
        source_id = rel.get("source")
        target_id = rel.get("target")
        if not all([rel_type, source_id, target_id]):
            logger.warning("graph_writer: skipping incomplete relationship: %s", rel)
            continue
        props = _clean_props(rel.get("properties") or {})
        row = {"source_id": source_id, "target_id": target_id, "props": props}

        src_desc = _descriptor(source_id)
        tgt_desc = _descriptor(target_id)
        if src_desc is None or tgt_desc is None:
            unresolved.append({**row, "type": rel_type})
            continue
        by_group[(rel_type, src_desc, tgt_desc)].append(row)

    total = 0
    for (rel_type, src_desc, tgt_desc), items in by_group.items():
        try:
            _validate_identifier(rel_type, "relationship type")
            src_pattern, src_where = _endpoint_match("a", "source_id", src_desc)
            tgt_pattern, tgt_where = _endpoint_match("b", "target_id", tgt_desc)
        except ValueError as exc:
            logger.warning("graph_writer: skipping %d relationships — %s", len(items), exc)
            continue

        where_parts = [w for w in (src_where, tgt_where) if w]
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        params: dict[str, Any] = {}
        if src_desc[0] == "eid":
            params["a_eid"] = src_desc[1]
        if tgt_desc[0] == "eid":
            params["b_eid"] = tgt_desc[1]

        for i in range(0, len(items), _BATCH_SIZE):
            batch = items[i : i + _BATCH_SIZE]
            result = session.run(
                f"""
                UNWIND $batch AS row
                MATCH {src_pattern}, {tgt_pattern}
                {where_clause}
                MERGE (a)-[r:{rel_type}]->(b)
                SET r += row.props
                RETURN count(*) AS written
                """,
                batch=batch,
                **params,
            )
            written = (result.single() or {}).get("written", 0)
            skipped = len(batch) - written
            if skipped > 0:
                logger.warning(
                    "graph_writer: %d relationships skipped (missing nodes) for type %s",
                    skipped, rel_type,
                )
            elif skipped < 0:
                logger.debug(
                    "graph_writer: type %s written=%d batch=%d (cartesian product hit)",
                    rel_type, written, len(batch),
                )
            total += written

    return total, unresolved


def _write_relationships_unlabelled(session, rows: list[dict]) -> int:
    """
    Fallback for relationships whose source or target id has no known label
    (not among this payload's own nodes) — matches on id alone, across every
    label, same as before the labelled-match path was added.
    """
    from collections import defaultdict

    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["type"]].append(row)

    total = 0
    for rel_type, items in by_type.items():
        try:
            _validate_identifier(rel_type, "relationship type")
        except ValueError as exc:
            logger.warning("graph_writer: skipping %d relationships — %s", len(items), exc)
            continue

        for i in range(0, len(items), _BATCH_SIZE):
            batch = items[i : i + _BATCH_SIZE]
            result = session.run(
                f"""
                UNWIND $batch AS row
                MATCH (a {{id: row.source_id}}), (b {{id: row.target_id}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r += row.props
                RETURN count(*) AS written
                """,
                batch=batch,
            )
            written = (result.single() or {}).get("written", 0)
            skipped = len(batch) - written
            if skipped > 0:
                logger.warning(
                    "graph_writer: %d relationships skipped (missing nodes) for type %s",
                    skipped, rel_type,
                )
            elif skipped < 0:
                logger.debug(
                    "graph_writer: type %s written=%d batch=%d (cartesian product hit)",
                    rel_type, written, len(batch),
                )
            total += written

    return total


def _write_relationships_batched(
    session, relationships: list[dict], id_to_labels: dict[str, list[str]]
) -> tuple[int, int, int]:
    """
    Write relationships, preferring a labelled MATCH (id unique index) for
    every endpoint whose label is known from the payload, falling back to
    an unlabelled match for the rest.

    Returns (rows_sent, rels_merged, rows_via_fallback):
      - rows_sent: relationship dicts received (the payload's own count,
        before any filtering — stable across code changes, so it's always
        the right number to compare against a previous run's).
      - rels_merged: sum of count(*) returned by every MERGE query, labelled
        and fallback combined. This is "rows that matched/merged", not a
        count of distinct relationships: a row whose endpoints match more
        than one physical node sharing the same id (e.g. a leftover
        duplicate-id node from a past failed ingestion — see
        delete_document_by_id) is counted once per match, same as before
        this function grouped by label. Switching to a labelled MATCH
        removes exactly that kind of over-count for the labels with a real
        id constraint, so this number dropping relative to an old unlabelled
        total does not by itself mean relationships went missing — compare
        rows_sent instead, or count relationships by type directly in the
        graph.
      - rows_via_fallback: rows whose endpoint had no known label and used
        the slower, unlabelled match.
    """
    rows_sent = len(relationships)
    written, unresolved = _write_relationships_labelled(session, relationships, id_to_labels)
    rows_via_fallback = len(unresolved)
    if unresolved:
        logger.warning(
            "graph_writer: %d relationship(s) fell back to unlabelled match "
            "(endpoint not found among this payload's nodes)",
            rows_via_fallback,
        )
        written += _write_relationships_unlabelled(session, unresolved)
    return rows_sent, written, rows_via_fallback


def _normalize_title(title: str) -> str:
    """
    Normalise a document title for fuzzy matching:
    - lowercase + strip whitespace
    - remove years (4-digit numbers)
    - remove common Italian legal suffixes and edition markers
    - collapse multiple spaces
    """
    import re as _re
    t = title.lower().strip()
    # Remove years (e.g. 2022, 2026)
    t = _re.sub(r'\b\d{4}\b', '', t)
    # Remove edition/version markers
    t = _re.sub(r'\b(aggiornato|aggiornata|edizione|ed\.|versione|vers\.|n\.|nr\.)\b', '', t)
    # Remove punctuation except spaces
    t = _re.sub(r'[^\w\s]', ' ', t)
    # Collapse spaces
    t = _re.sub(r'\s+', ' ', t).strip()
    return t


def find_document_by_name(name: str) -> list[dict]:
    """
    Return all LEGAL_DOC nodes whose title matches the given name.

    Matching: exact match after normalisation (lowercase, strip years/punctuation),
    so "Codice Penale - 2026" matches "Codice Penale 2025". Callers delete what is
    returned, so titles that only contain one another never match: that deleted
    "Codice Penale Commentato ADE" when "Codice Penale" was uploaded, and a title
    that normalises to nothing would have matched every document.

    Returns a list of dicts with keys: id, name, hash, file_name.
    Empty list if none found or Neo4J is not configured.
    """
    if not is_configured():
        return []
    driver = _get_driver()
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            # Fetch all LEGAL_DOC nodes — we do the fuzzy match in Python
            # since Cypher has limited string normalisation capabilities.
            # The corpus is typically < 10k documents so this is fast.
            result = session.run(
                """
                MATCH (d:Document)
                WHERE d.name IS NOT NULL AND trim(d.name) <> ''
                RETURN d.id AS id, d.name AS name, d.hash AS hash,
                       d.file_name AS file_name
                """
            )
            all_docs = [dict(r) for r in result]

        new_norm = _normalize_title(name)
        matches = []
        for doc in all_docs:
            existing_norm = _normalize_title(doc["name"] or "")
            if not existing_norm:
                continue
            # Exact normalised match only — substring matching caused
            # false positives (e.g. "Codice Penale" deleting "Codice Penale Commentato ADE")
            if new_norm == existing_norm:
                matches.append(doc)
                logger.debug(
                    "find_document_by_name: %r matched existing %r "
                    "(normalised: %r vs %r)",
                    name, doc["name"], new_norm, existing_norm,
                )
        return matches
    finally:
        driver.close()


def delete_document_by_id(doc_node_id: str) -> dict:
    """
    Delete a LEGAL_DOC node and all nodes reachable from it
    (DOCUMENT_SECTION, EDITOR, LEGAL_SOURCE if orphaned).
    Returns counts of deleted nodes and relationships.

    Safe to call with a non-existent id — returns zeros.
    """
    if not is_configured():
        return {"nodes": 0, "relationships": 0}
    driver = _get_driver()
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            # Run both deletes inside a single explicit transaction so a
            # connection drop / timeout / interrupt between the two steps
            # cannot leave sections orphaned (relationship gone, node intact,
            # parent Document gone) — either both succeed together or neither
            # commits, preventing the exact failure mode that orphaned 6,327
            # sections across 6 deleted documents in production.
            def _do_delete(tx):
                section_result = tx.run(
                    """
                    MATCH (d:Document {id: $doc_id})-[:CONTAINS]->(s:Section)
                    DETACH DELETE s
                    RETURN count(s) AS deleted
                    """,
                    doc_id=doc_node_id,
                )
                sections_deleted = (section_result.single() or {}).get("deleted", 0)

                # Never-relabelled DOCUMENT_SECTION nodes of this document, left by an
                # ingestion that failed after writing: no Section label, so the delete
                # above misses them and the next ingestion would relabel them into
                # extra copies. Their id carries the document hash; the id constraint
                # on DOCUMENT_SECTION makes this prefix lookup an index seek.
                if doc_node_id.startswith("LEGAL_DOC::"):
                    stale_result = tx.run(
                        """
                        MATCH (s:DOCUMENT_SECTION) WHERE s.id STARTS WITH $prefix
                        DETACH DELETE s
                        RETURN count(s) AS deleted
                        """,
                        prefix=f"DOCUMENT_SECTION::{doc_node_id.split('::', 1)[1]}::",
                    )
                    sections_deleted += (stale_result.single() or {}).get("deleted", 0)

                doc_result = tx.run(
                    """
                    MATCH (d:Document {id: $doc_id})
                    DETACH DELETE d
                    RETURN count(d) AS deleted
                    """,
                    doc_id=doc_node_id,
                )
                docs_deleted = (doc_result.single() or {}).get("deleted", 0)
                return sections_deleted, docs_deleted

            sections_deleted, docs_deleted = session.execute_write(_do_delete)

            # Use WARNING so it appears even when basicConfig is set to ERROR level
            logger.warning(
                "graph_writer: deleted document %s — %d sections, %d doc nodes",
                doc_node_id, sections_deleted, docs_deleted,
            )
            return {"nodes": sections_deleted + docs_deleted, "relationships": 0}
    finally:
        driver.close()


def write_graph_payload(payload: dict[str, Any]) -> tuple[int, int]:
    """
    Write a graph payload to Neo4J.

    Nodes are MERGEd on their ``id`` property — re-running is safe and will
    update (SET +=) any changed properties without creating duplicates.

    Relationships are MERGEd on (source)-[TYPE]->(target) — properties are
    updated the same way.

    On first call, uniqueness constraints are created for all known labels so
    that subsequent MERGE operations use an index lookup instead of a full scan.

    Writes are batched using UNWIND — one round-trip per _BATCH_SIZE items
    per label/type, dramatically reducing network overhead for large documents.

    Args:
        payload: Dict with "nodes" and "relationships" lists, as produced by
                 lex_package.utils.flatten.build_neo4j_graph_payload().

    Returns:
        (nodes_written, relationships_written)

    Raises:
        RuntimeError: If NEO4J_URI is not set.
        ImportError:  If the neo4j package is not installed.
    """
    nodes = payload.get("nodes", [])
    relationships = payload.get("relationships", [])

    if not nodes and not relationships:
        logger.warning("graph_writer: empty payload — nothing to write.")
        return 0, 0

    driver = _get_driver()
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    nodes_written = 0
    rels_written = 0

    try:
        with driver.session(database=database) as session:
            # ── Ensure constraints/indexes exist ──────────────────────────────
            _ensure_constraints(session)

            # ── Write nodes (batched) ─────────────────────────────────────────
            nodes_written = _write_nodes_batched(session, nodes)

            # ── Write relationships (batched) ─────────────────────────────────
            id_to_labels = {
                node["id"]: node.get("labels") or []
                for node in nodes
                if node.get("id")
            }
            rel_rows_sent, rels_written, rel_rows_fallback = _write_relationships_batched(
                session, relationships, id_to_labels
            )

    finally:
        driver.close()

    logger.info(
        "graph_writer: wrote %d nodes and %d relationships to Neo4J (%s) "
        "— relationship rows sent: %d, merged: %d, via unlabelled fallback: %d.",
        nodes_written,
        rels_written,
        os.environ.get("NEO4J_URI", ""),
        rel_rows_sent,
        rels_written,
        rel_rows_fallback,
    )
    return nodes_written, rels_written
