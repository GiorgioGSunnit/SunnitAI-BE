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
        label_str = ":".join(labels)
        props = _clean_props(node.get("properties", {}))
        props["id"] = node_id  # ensure id is in the props map for SET +=
        by_label[label_str].append({"id": node_id, "props": props})

    total = 0
    for label_str, items in by_label.items():
        for i in range(0, len(items), _BATCH_SIZE):
            batch = items[i : i + _BATCH_SIZE]
            session.run(
                f"""
                UNWIND $batch AS row
                MERGE (n:{label_str} {{id: row.id}})
                SET n += row.props
                """,
                batch=batch,
            )
            total += len(batch)

    return total


def _write_relationships_batched(session, relationships: list[dict]) -> int:
    """
    Write relationships in UNWIND batches grouped by relationship type.
    Returns total relationships written.
    """
    from collections import defaultdict

    by_type: dict[str, list[dict]] = defaultdict(list)
    for rel in relationships:
        rel_type = rel.get("type")
        source_id = rel.get("source")
        target_id = rel.get("target")
        if not all([rel_type, source_id, target_id]):
            logger.warning("graph_writer: skipping incomplete relationship: %s", rel)
            continue
        props = _clean_props(rel.get("properties") or {})
        by_type[rel_type].append({
            "source_id": source_id,
            "target_id": target_id,
            "props": props,
        })

    total = 0
    for rel_type, items in by_type.items():
        for i in range(0, len(items), _BATCH_SIZE):
            batch = items[i : i + _BATCH_SIZE]
            session.run(
                f"""
                UNWIND $batch AS row
                MATCH (a {{id: row.source_id}}), (b {{id: row.target_id}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r += row.props
                """,
                batch=batch,
            )
            total += len(batch)

    return total


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
            rels_written = _write_relationships_batched(session, relationships)

    finally:
        driver.close()

    logger.info(
        "graph_writer: wrote %d nodes and %d relationships to Neo4J (%s).",
        nodes_written,
        rels_written,
        os.environ.get("NEO4J_URI", ""),
    )
    return nodes_written, rels_written
