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


def write_graph_payload(payload: dict[str, Any]) -> tuple[int, int]:
    """
    Write a graph payload to Neo4J.

    Nodes are MERGEd on their ``id`` property — re-running is safe and will
    update (SET +=) any changed properties without creating duplicates.

    Relationships are MERGEd on (source)-[TYPE]->(target) — properties are
    updated the same way.

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

            # ── Write nodes ───────────────────────────────────────────────────
            for node in nodes:
                node_id = node.get("id")
                if not node_id:
                    logger.warning("graph_writer: skipping node without id: %s", node)
                    continue

                labels = node.get("labels") or ["Node"]
                # Filter out None values — Neo4J driver rejects them
                props = {
                    k: v
                    for k, v in node.get("properties", {}).items()
                    if v is not None
                }
                # Truncate vectors to avoid hitting Neo4J's 4MB property limit
                if "vector" in props and isinstance(props["vector"], list):
                    props["vector"] = [float(x) for x in props["vector"][:1536]]

                label_str = ":".join(labels)
                session.run(
                    f"MERGE (n:{label_str} {{id: $id}}) SET n += $props",
                    id=node_id,
                    props=props,
                )
                nodes_written += 1

            # ── Write relationships ───────────────────────────────────────────
            for rel in relationships:
                rel_type = rel.get("type")
                source_id = rel.get("source")
                target_id = rel.get("target")
                if not all([rel_type, source_id, target_id]):
                    logger.warning("graph_writer: skipping incomplete relationship: %s", rel)
                    continue

                props = {
                    k: v
                    for k, v in (rel.get("properties") or {}).items()
                    if v is not None
                }
                session.run(
                    f"""
                    MATCH (a {{id: $source_id}}), (b {{id: $target_id}})
                    MERGE (a)-[r:{rel_type}]->(b)
                    SET r += $props
                    """,
                    source_id=source_id,
                    target_id=target_id,
                    props=props,
                )
                rels_written += 1

    finally:
        driver.close()

    logger.info(
        "graph_writer: wrote %d nodes and %d relationships to Neo4J (%s).",
        nodes_written,
        rels_written,
        os.environ.get("NEO4J_URI", ""),
    )
    return nodes_written, rels_written
