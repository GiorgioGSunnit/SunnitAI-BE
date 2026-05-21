"""
Post-ingestion processing for Neo4j graph data.

Called after every successful document ingestion to:
  1. Set required properties on DOCUMENT_SECTION nodes (before relabeling)
  2. Relabel uppercase nodes to PascalCase (scoped to current document)
  2b. Fix Section node name and abstract, clear embeddings for re-generation
  3. Generate embeddings for new Section nodes using sentence-transformers

Each step is wrapped in try/except — a failure in one step does not abort
the others. Returns a summary dict for logging/debugging.
"""
from __future__ import annotations

import logging
import os

from lex_package.utils.embeddings import embed_texts_batch, embeddings_enabled

logger = logging.getLogger("lex_package.post_process")

# ── Label mapping (SCREAMING_SNAKE_CASE → PascalCase) ─────────────────────────

_LABEL_MAPPING: dict[str, str] = {
    "DOCUMENT_SECTION": "Section",
    "LEGAL_DOC":        "Document",
    "LEGAL_SOURCE":     "LegalAct",
    "LEGAL_CONCEPT":    "Topic",
    "LEGAL_ACTION":     "Penalty",
    "ORGANIZATION":     "Institution",
    "PERSON":           "Person",
    "EDITOR":           "Editor",
    "DATE":             "Date",
    "ROLE":             "Role",
    "LOCATION":         "Location",
}


def _to_pascal(label: str) -> str:
    """Convert SCREAMING_SNAKE_CASE to PascalCase (e.g. LEGAL_EDITOR → LegalEditor)."""
    return "".join(word.capitalize() for word in label.split("_"))


def _get_driver():
    from neo4j import GraphDatabase
    uri = os.environ.get("NEO4J_URI")
    if not uri:
        raise RuntimeError("NEO4J_URI is not set — cannot run post-processing.")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    return GraphDatabase.driver(uri, auth=(user, password))


# ── Step 1 ─────────────────────────────────────────────────────────────────────

def _step1_set_properties(session, document_hash: str) -> str:
    """
    Ensure every DOCUMENT_SECTION node for this document has plain_text,
    abstract, and name populated before relabeling.
    """
    try:
        prefix = f"DOCUMENT_SECTION::{document_hash}"
        session.run(
            """
            MATCH (n:DOCUMENT_SECTION)
            WHERE n.id STARTS WITH $prefix
            SET n.plain_text = coalesce(n.plain_text, n.text, n.content, n.body, ""),
                n.abstract   = coalesce(n.abstract,   n.summary, n.text, n.content, ""),
                n.name       = coalesce(n.name,        n.title,  n.id,   "")
            """,
            prefix=prefix,
        )
        logger.info("post_process step1: properties set for document %s", document_hash)
        return "ok"
    except Exception as exc:
        logger.error("post_process step1 failed: %s", exc)
        return f"error: {exc}"


# ── Step 2 ─────────────────────────────────────────────────────────────────────

def _step2_relabel(session, document_hash: str) -> str:
    """
    Relabel all uppercase nodes belonging to this document using _LABEL_MAPPING.
    Any uppercase label not in the mapping is auto-converted to PascalCase.
    """
    try:
        # Collect all labels currently on nodes that belong to this document
        result = session.run(
            """
            MATCH (n)
            WHERE n.id CONTAINS $document_hash
            UNWIND labels(n) AS lbl
            RETURN DISTINCT lbl
            """,
            document_hash=document_hash,
        )
        existing_labels = [row["lbl"] for row in result]

        # Build effective mapping for this document's labels
        effective: dict[str, str] = {}
        for lbl in existing_labels:
            if lbl in _LABEL_MAPPING:
                effective[lbl] = _LABEL_MAPPING[lbl]
            elif lbl.isupper() or (lbl == lbl.upper() and "_" in lbl):
                # Unknown uppercase label — auto-convert
                effective[lbl] = _to_pascal(lbl)
            # Already PascalCase or mixed — leave as-is

        relabeled = 0
        skipped = 0
        for old_label, new_label in effective.items():
            if old_label == new_label:
                continue
            try:
                session.run(
                    f"""
                    MATCH (n:{old_label})
                    WHERE n.id CONTAINS $document_hash
                    REMOVE n:{old_label}
                    SET n:{new_label}
                    """,
                    document_hash=document_hash,
                )
                logger.debug("post_process step2: %s → %s", old_label, new_label)
                relabeled += 1
            except Exception as exc:
                # Constraint violation: node already has the target label from a previous run
                logger.warning(
                    "post_process step2: skipped %s → %s (constraint or error: %s)",
                    old_label, new_label, exc,
                )
                skipped += 1

        logger.info(
            "post_process step2: relabeled %d label type(s) for document %s",
            relabeled, document_hash,
        )
        return "ok"
    except Exception as exc:
        logger.error("post_process step2 failed: %s", exc)
        return f"error: {exc}"


# ── Step 2b helpers ────────────────────────────────────────────────────────────

def _clean_plain_text(text: str) -> str:
    if not text or len(text) < 100:
        return text

    lines = text.split("\n")

    # Find first "substantial" line — longer than 100 chars or
    # starts a numbered list (1. 2. etc) or contains legal keywords
    legal_keywords = [
        "articolo", "comma", "legge", "decreto", "requisiti",
        "regime", "soggetti", "contratto", "diritto", "obbligo",
    ]

    for i, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) > 100:
            return "\n".join(lines[i:]).strip()
        if any(kw in stripped.lower() for kw in legal_keywords) and len(stripped) > 30:
            return "\n".join(lines[i:]).strip()

    return text


# ── Step 2b ────────────────────────────────────────────────────────────────────

def _step2b_fix_section_properties(session, document_hash: str) -> str:
    """
    Fix name and abstract on Section nodes after relabeling:
    - name: replace "0" with a sequential index based on elementId order
    - abstract: strip noise prefixes like "0 Testo significativo:"
    - clear embeddings so Step 3 re-generates them with clean text
    """
    try:
        # Fix name — sequential numbering ordered by elementId
        session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            WHERE d.hash = $document_hash
            WITH s ORDER BY elementId(s)
            WITH collect(s) AS sections
            UNWIND range(0, size(sections)-1) AS i
            WITH sections[i] AS s, i+1 AS idx
            SET s.name = toString(idx)
            """,
            document_hash=document_hash,
        )

        # Fix abstract — strip noise text
        session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            WHERE d.hash = $document_hash
            SET s.abstract = trim(
                replace(
                    replace(
                        replace(s.abstract, "Testo significativo:", ""),
                        "0 ", ""
                    ),
                    "  ", " "
                )
            )
            """,
            document_hash=document_hash,
        )

        # Fall back to plain_text if abstract is too short after cleaning
        session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            WHERE d.hash = $document_hash
              AND size(trim(s.abstract)) < 20
            SET s.abstract = left(s.plain_text, 200)
            """,
            document_hash=document_hash,
        )

        # Clean plain_text — strip short header/navigation lines in Python
        rows = session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            WHERE d.hash = $document_hash
              AND s.plain_text IS NOT NULL AND s.plain_text <> ""
            RETURN elementId(s) AS eid, s.plain_text AS plain_text
            """,
            document_hash=document_hash,
        ).data()
        for row in rows:
            cleaned = _clean_plain_text(row["plain_text"])
            if cleaned != row["plain_text"]:
                session.run(
                    "MATCH (s) WHERE elementId(s) = $eid SET s.plain_text = $text",
                    eid=row["eid"],
                    text=cleaned,
                )
        logger.debug("post_process step2b: cleaned plain_text for %d sections", len(rows))

        # Clear embeddings so Step 3 re-generates with clean text
        session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            WHERE d.hash = $document_hash
            REMOVE s.embedding
            """,
            document_hash=document_hash,
        )

        logger.info("post_process step2b: fixed name/abstract and cleared embeddings for document %s", document_hash)
        return "ok"
    except Exception as exc:
        logger.error("post_process step2b failed: %s", exc)
        return f"error: {exc}"


# ── Step 3 ─────────────────────────────────────────────────────────────────────

def _step3_generate_embeddings(session, document_hash: str) -> str:
    """
    Generate embeddings for Section nodes belonging to the current document
    that have plain_text but no embedding (step 2b cleared them before this runs).
    """
    try:
        result = session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            WHERE (d.hash = $document_hash OR d.id CONTAINS $document_hash)
              AND (s.embedding IS NULL OR size(s.embedding) = 0)
              AND s.plain_text IS NOT NULL
              AND s.plain_text <> ""
            RETURN elementId(s) AS element_id,
                   coalesce(s.abstract, "") + " " + coalesce(s.plain_text, "") AS text
            """,
            document_hash=document_hash,
        )
        nodes = result.data()

        if not nodes:
            logger.info("post_process step3: no Section nodes need embeddings")
            return "0 embeddings generated"

        if not embeddings_enabled():
            return "0 embeddings generated (disabled)"

        texts = [n["text"] for n in nodes]
        embeddings = embed_texts_batch(texts)
        total = 0

        for node, embedding in zip(nodes, embeddings):
            if not embedding:
                continue
            session.run(
                "MATCH (s) WHERE elementId(s) = $eid SET s.embedding = $emb",
                eid=node["element_id"],
                emb=embedding,
            )
            total += 1

        logger.info("post_process step3: generated %d embeddings", total)
        return f"{total} embeddings generated"
    except Exception as exc:
        logger.error("post_process step3 failed: %s", exc)
        return f"error: {exc}"


# ── Public entry points ────────────────────────────────────────────────────────

def post_process_fast_steps(document_hash: str) -> dict[str, str]:
    """
    Run only the fast graph-fixup steps (0 → 2b) for a successfully ingested
    document. Does NOT generate embeddings.

    Intended to be called synchronously inside the ingestion job so the job
    can be marked Completed before the CPU-heavy embedding step runs.

    Returns a summary dict with keys: step0, step1, step2, step2c, step2b.
    """
    logger.info("post_process_fast_steps START — document_hash=%s", document_hash)
    summary: dict[str, str] = {}

    try:
        driver = _get_driver()
    except Exception as exc:
        logger.error("post_process: cannot connect to Neo4J: %s", exc)
        return {k: f"error: {exc}" for k in ("step0", "step1", "step2", "step2c", "step2b")}

    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            try:
                session.run(
                    """
                    MATCH (d)-[:CONTAINS]->(s:Section)
                    WHERE d.hash = $document_hash
                    DETACH DELETE s
                    """,
                    document_hash=document_hash,
                )
                logger.info("post_process step0: deleted existing sections for document %s", document_hash)
                summary["step0"] = "ok"
            except Exception as exc:
                logger.error("post_process step0 failed: %s", exc)
                summary["step0"] = f"error: {exc}"

            summary["step1"] = _step1_set_properties(session, document_hash)
            summary["step2"] = _step2_relabel(session, document_hash)

            try:
                remaining = session.run(
                    """
                    MATCH (n)
                    WHERE n.hash = $hash OR n.id CONTAINS $hash
                    UNWIND labels(n) AS label
                    WITH label WHERE label = toUpper(label)
                    RETURN count(*) AS uppercase_remaining
                    """,
                    hash=document_hash,
                ).single()
                count = remaining["uppercase_remaining"] if remaining else 0
                if count > 0:
                    logger.warning(
                        "post_process: relabeling incomplete — %d uppercase nodes still exist for %s",
                        count, document_hash,
                    )
                else:
                    logger.info("post_process: relabeling verified — no uppercase nodes remaining for %s", document_hash)
            except Exception as exc:
                logger.error("post_process: relabeling verification failed: %s", exc)

            try:
                result = session.run(
                    """
                    MATCH (d:Document)-[:CONTAINS]->(s:Section)
                    WHERE d.hash = $hash OR d.id CONTAINS $hash
                    RETURN count(s) AS section_count
                    """,
                    hash=document_hash,
                ).single()
                count = result["section_count"] if result else 0
                logger.info("post_process: sections linked to Document node: %d", count)
                if count == 0:
                    logger.warning(
                        "post_process: no sections linked to Document node for %s — CONTAINS relationships may be on wrong node",
                        document_hash,
                    )
            except Exception as exc:
                logger.error("post_process: section verification failed: %s", exc)

            try:
                result = session.run(
                    """
                    MATCH (d:Document)
                    WHERE d.hash = $document_hash OR d.id CONTAINS $document_hash
                    MATCH (s:Section)
                    WHERE s.id CONTAINS $document_hash
                      AND NOT EXISTS { MATCH (d)-[:CONTAINS]->(s) }
                    MERGE (d)-[:CONTAINS]->(s)
                    RETURN count(s) AS repaired
                    """,
                    document_hash=document_hash,
                ).single()
                repaired = result["repaired"] if result else 0
                if repaired > 0:
                    logger.warning(
                        "post_process step2c: repaired %d missing CONTAINS relationships for document %s",
                        repaired, document_hash,
                    )
                else:
                    logger.info("post_process step2c: all CONTAINS relationships intact for document %s", document_hash)
                summary["step2c"] = f"repaired {repaired}" if repaired > 0 else "ok"
            except Exception as exc:
                logger.error("post_process step2c failed: %s", exc)
                summary["step2c"] = f"error: {exc}"

            summary["step2b"] = _step2b_fix_section_properties(session, document_hash)
    finally:
        driver.close()

    logger.info("post_process_fast_steps COMPLETE — document_hash=%s summary=%s", document_hash, summary)
    return summary


def post_process_embeddings(document_hash: str) -> str:
    """
    Generate embeddings for Section nodes of the given document.

    Intended to be submitted to a background executor *after* the ingestion
    job has already been marked Completed, so CPU-heavy encoding does not
    block the job lifecycle or API responses.

    Returns a status string (e.g. "42 embeddings generated").
    """
    logger.info("post_process_embeddings START — document_hash=%s", document_hash)
    try:
        driver = _get_driver()
    except Exception as exc:
        logger.error("post_process_embeddings: cannot connect to Neo4J: %s", exc)
        return f"error: {exc}"

    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    result = "error: unknown"
    try:
        # Brief pause to ensure step 2b's cleared embeddings are committed
        # and visible before step 3 queries for NULL/empty embeddings.
        import time as _time
        _time.sleep(1.0)
        with driver.session(database=database) as session:
            result = _step3_generate_embeddings(session, document_hash)
    except Exception as exc:
        logger.error("post_process_embeddings: session error for document %s: %s", document_hash, exc)
        result = f"error: {exc}"
    finally:
        driver.close()

    logger.info("post_process_embeddings COMPLETE — document_hash=%s result=%s", document_hash, result)
    return result


def post_process_ingestion(document_hash: str) -> dict[str, str]:
    """
    Run post-ingestion processing for a successfully ingested document.

    Steps run in order; a failure in one step is logged and skipped —
    it does not abort the remaining steps.

    Args:
        document_hash: SHA256 hash of the ingested PDF (used to scope all queries
                       to nodes belonging to the current document only).

    Returns:
        {
            "step0":  "ok" | "error: ...",
            "step1":  "ok" | "error: ...",
            "step2":  "ok" | "error: ...",
            "step2c": "ok" | "repaired N" | "error: ...",
            "step2b": "ok" | "error: ...",
            "step3":  "N embeddings generated" | "error: ...",
        }
    """
    logger.info("post_process_ingestion START — document_hash=%s", document_hash)
    summary: dict[str, str] = {}

    try:
        driver = _get_driver()
    except Exception as exc:
        logger.error("post_process: cannot connect to Neo4J: %s", exc)
        return {"step0": f"error: {exc}", "step1": f"error: {exc}", "step2": f"error: {exc}", "step2b": f"error: {exc}", "step3": f"error: {exc}"}

    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            # ── Step 0 — Delete existing sections to prevent duplicates on re-ingestion
            try:
                session.run(
                    """
                    MATCH (d)-[:CONTAINS]->(s:Section)
                    WHERE d.hash = $document_hash
                    DETACH DELETE s
                    """,
                    document_hash=document_hash,
                )
                logger.info("post_process step0: deleted existing sections for document %s", document_hash)
                summary["step0"] = "ok"
            except Exception as exc:
                logger.error("post_process step0 failed: %s", exc)
                summary["step0"] = f"error: {exc}"

            summary["step1"]  = _step1_set_properties(session, document_hash)
            summary["step2"]  = _step2_relabel(session, document_hash)

            # ── Fix 3 — Verify relabeling completed
            try:
                remaining = session.run(
                    """
                    MATCH (n)
                    WHERE n.hash = $hash OR n.id CONTAINS $hash
                    UNWIND labels(n) AS label
                    WITH label WHERE label = toUpper(label)
                    RETURN count(*) AS uppercase_remaining
                    """,
                    hash=document_hash,
                ).single()
                count = remaining["uppercase_remaining"] if remaining else 0
                if count > 0:
                    logger.warning("post_process: relabeling incomplete — %d uppercase nodes still exist for %s", count, document_hash)
                else:
                    logger.info("post_process: relabeling verified — no uppercase nodes remaining for %s", document_hash)
            except Exception as exc:
                logger.error("post_process: relabeling verification failed: %s", exc)

            # ── Fix 4 — Verify sections are linked to Document node
            try:
                result = session.run(
                    """
                    MATCH (d:Document)-[:CONTAINS]->(s:Section)
                    WHERE d.hash = $hash OR d.id CONTAINS $hash
                    RETURN count(s) AS section_count
                    """,
                    hash=document_hash,
                ).single()
                count = result["section_count"] if result else 0
                logger.info("post_process: sections linked to Document node: %d", count)
                if count == 0:
                    logger.warning("post_process: no sections linked to Document node for %s — CONTAINS relationships may be on wrong node", document_hash)
            except Exception as exc:
                logger.error("post_process: section verification failed: %s", exc)

            # ── Step 2c — Repair missing CONTAINS relationships
            try:
                result = session.run(
                    """
                    MATCH (d:Document)
                    WHERE d.hash = $document_hash OR d.id CONTAINS $document_hash
                    MATCH (s:Section)
                    WHERE s.id CONTAINS $document_hash
                      AND NOT EXISTS { MATCH (d)-[:CONTAINS]->(s) }
                    MERGE (d)-[:CONTAINS]->(s)
                    RETURN count(s) AS repaired
                    """,
                    document_hash=document_hash,
                ).single()
                repaired = result["repaired"] if result else 0
                if repaired > 0:
                    logger.warning("post_process step2c: repaired %d missing CONTAINS relationships for document %s", repaired, document_hash)
                else:
                    logger.info("post_process step2c: all CONTAINS relationships intact for document %s", document_hash)
                summary["step2c"] = f"repaired {repaired}" if repaired > 0 else "ok"
            except Exception as exc:
                logger.error("post_process step2c failed: %s", exc)
                summary["step2c"] = f"error: {exc}"

            summary["step2b"] = _step2b_fix_section_properties(session, document_hash)
            summary["step3"]  = _step3_generate_embeddings(session, document_hash)
    finally:
        driver.close()

    logger.info("post_process_ingestion COMPLETE — document_hash=%s summary=%s", document_hash, summary)
    return summary
