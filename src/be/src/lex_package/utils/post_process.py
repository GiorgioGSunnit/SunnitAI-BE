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
        for old_label, new_label in effective.items():
            if old_label == new_label:
                continue
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

        logger.info(
            "post_process step2: relabeled %d label type(s) for document %s",
            relabeled, document_hash,
        )
        return "ok"
    except Exception as exc:
        logger.error("post_process step2 failed: %s", exc)
        return f"error: {exc}"


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
        from sentence_transformers import SentenceTransformer

        result = session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            WHERE d.hash = $document_hash
              AND s.embedding IS NULL
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

        model = SentenceTransformer("BAAI/bge-large-en-v1.5")
        batch_size = 100
        total = 0

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]
            texts = [n["text"] for n in batch]
            embeddings = model.encode(texts, normalize_embeddings=True)

            for node, embedding in zip(batch, embeddings):
                session.run(
                    "MATCH (s) WHERE elementId(s) = $eid SET s.embedding = $emb",
                    eid=node["element_id"],
                    emb=embedding.tolist(),
                )
            total += len(batch)
            logger.debug(
                "post_process step3: embedded %d/%d nodes",
                min(i + batch_size, len(nodes)), len(nodes),
            )

        logger.info("post_process step3: generated %d embeddings", total)
        return f"{total} embeddings generated"
    except Exception as exc:
        logger.error("post_process step3 failed: %s", exc)
        return f"error: {exc}"


# ── Public entry point ─────────────────────────────────────────────────────────

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
            "step1":  "ok" | "error: ...",
            "step2":  "ok" | "error: ...",
            "step2b": "ok" | "error: ...",
            "step3":  "N embeddings generated" | "error: ...",
        }
    """
    logger.info("post_process: starting for document_hash=%s", document_hash)
    summary: dict[str, str] = {}

    try:
        driver = _get_driver()
    except Exception as exc:
        logger.error("post_process: cannot connect to Neo4J: %s", exc)
        return {"step1": f"error: {exc}", "step2": f"error: {exc}", "step2b": f"error: {exc}", "step3": f"error: {exc}"}

    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            summary["step1"]  = _step1_set_properties(session, document_hash)
            summary["step2"]  = _step2_relabel(session, document_hash)
            summary["step2b"] = _step2b_fix_section_properties(session, document_hash)
            summary["step3"]  = _step3_generate_embeddings(session, document_hash)
    finally:
        driver.close()

    logger.info("post_process: completed — %s", summary)
    return summary
