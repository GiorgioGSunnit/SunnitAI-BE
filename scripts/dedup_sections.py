#!/usr/bin/env python3
"""
Remove duplicate Section nodes of a document: same document, same section name, same
(whitespace-normalised) plain_text. The corpus has up to 7 copies of some articles; they
score identically, take each other's places in search and citations, and inflate counts.

DRY RUN BY DEFAULT: reads only and prints what it would do. --apply writes.

For each group of copies (within one Document node) it keeps one node:
  1. the one that has embedding_v2 (what V2 searches),
  2. then the one with the most relationships,
  3. then the lowest element id (deterministic);
then re-points the other copies' relationships to it (MERGE, so nothing is duplicated;
relationship properties are copied) and deletes the other copies, one transaction per group.

Copies spread over two Document nodes with the same name are NOT merged (that is a
duplicate document, not a duplicate section); the dry run reports them.

Usage (BE virtualenv; .env is read from the folder above scripts/):
    venv/bin/python scripts/dedup_sections.py --doc "Codice Penale 2026" --doc "Codice della Privacy 2026"
    venv/bin/python scripts/dedup_sections.py --doc "Codice Penale 2026" --check-queries
    venv/bin/python scripts/dedup_sections.py --doc "Codice Penale 2026" --apply --target bolt://10.15.214.1:7687

  --check-queries  EXPLAIN the write queries on this database (validates them, writes nothing)
  --apply          write; needs --target equal to NEO4J_URI, so it can't hit another database by mistake
  --limit-groups   at most N groups per document (to try --apply on a few first)

--apply needs Neo4j 5.26 or later (dynamic relationship types). Run it on be1 first and
compare search before/after (scripts/compare_retrieval.py) before touching production;
back up production first.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=False)

SECTIONS = """
    MATCH (d:Document)-[:CONTAINS]->(s:Section) WHERE elementId(d) = $doc
    RETURN elementId(s) AS eid, s.id AS id, coalesce(s.name, '') AS name,
           coalesce(s.plain_text, '') AS text, s.embedding_v2 IS NOT NULL AS v2,
           COUNT { (s)--() } AS degree
"""
# Re-point the copies' relationships to the kept node, then delete the copies.
# Relationships between copies of the same group, or to the kept node, are dropped.
MOVE_OUT = """
    MATCH (k) WHERE elementId(k) = $keep
    MATCH (dup)-[r]->(o) WHERE elementId(dup) IN $dups AND o <> k AND NOT elementId(o) IN $dups
    WITH k, o, type(r) AS t, properties(r) AS p
    MERGE (k)-[r2:$(t)]->(o)
    SET r2 += p
"""
MOVE_IN = """
    MATCH (k) WHERE elementId(k) = $keep
    MATCH (o)-[r]->(dup) WHERE elementId(dup) IN $dups AND o <> k AND NOT elementId(o) IN $dups
    WITH k, o, type(r) AS t, properties(r) AS p
    MERGE (o)-[r2:$(t)]->(k)
    SET r2 += p
"""
DELETE = "MATCH (dup) WHERE elementId(dup) IN $dups DETACH DELETE dup"
WRITE_QUERIES = {"move outgoing": MOVE_OUT, "move incoming": MOVE_IN, "delete copies": DELETE}


def log(msg: str = "") -> None:
    print(msg, flush=True)


def group_key(name: str, text: str) -> tuple[str, str]:
    return name, " ".join(text.split())


def id_kind(node_id: str | None) -> str:
    """What the hash part of a Section id looks like (evidence for where copies came from)."""
    tail = (node_id or "").rsplit("::", 1)[-1]
    if re.fullmatch(r"-?\d+", tail):
        return "python-hash"    # Python's salted hash(): differs on every ingestion
    if re.fullmatch(r"[0-9a-f]{20}", tail):
        return "hex"            # sha256-based: deterministic
    return "other"


def plan_groups(rows: list[dict]) -> list[dict]:
    """Groups with more than one node, each with the node to keep and the copies to delete."""
    by_key = defaultdict(list)
    for r in rows:
        by_key[group_key(r["name"], r["text"])].append(r)
    groups = []
    for (name, _), members in by_key.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (not r["v2"], -r["degree"], r["eid"]))
        groups.append({"name": name, "keep": members[0], "dups": members[1:]})
    groups.sort(key=lambda g: (-len(g["dups"]), g["name"]))
    return groups


def neo4j_version(session) -> str:
    try:
        r = session.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()
        return r["v"] if r else "?"
    except Exception:
        return "?"


def supports_dynamic_types(version: str) -> bool:
    """Dynamic relationship types ($(t)) came with Neo4j 5.26; calendar versions (2025.x+) have them."""
    m = re.match(r"(\d+)\.(\d+)", version or "")
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return major >= 2025 or (major == 5 and minor >= 26)


def report(doc_name: str, doc: dict, rows: list[dict], groups: list[dict], show: int) -> None:
    dups = sum(len(g["dups"]) for g in groups)
    log(f"  Document node {doc['id']}: {len(rows)} sections, {len(groups)} groups with copies, "
        f"{dups} copies to delete ({len(rows) - dups} sections would remain)")
    if not groups:
        return
    kinds = Counter(id_kind(r["id"]) for g in groups for r in [g["keep"], *g["dups"]])
    log(f"    ids of the duplicated sections: {dict(kinds)}")
    lost_v2 = sum(1 for g in groups if not g["keep"]["v2"] and any(d["v2"] for d in g["dups"]))
    if lost_v2:
        log(f"    WARNING: {lost_v2} groups would keep a node without embedding_v2 (should be 0)")
    for g in groups[:show]:
        k = g["keep"]
        log(f"    {g['name'] or '(no name)'!s:<14} {len(g['dups']) + 1} copies; keep {k['id']} "
            f"(v2={k['v2']}, rels={k['degree']}); delete e.g. {[d['id'] for d in g['dups'][:2]]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove duplicate Section nodes (dry run by default).")
    parser.add_argument("--doc", action="append", required=True, help="exact Document name (repeatable)")
    parser.add_argument("--apply", action="store_true", help="write (needs --target)")
    parser.add_argument("--target", default=None, help="must equal NEO4J_URI for --apply")
    parser.add_argument("--check-queries", action="store_true", help="EXPLAIN the write queries; writes nothing")
    parser.add_argument("--limit-groups", type=int, default=None, help="at most N groups per document")
    parser.add_argument("--show", type=int, default=10, help="groups printed per document")
    args = parser.parse_args()

    uri = os.environ["NEO4J_URI"]
    if args.apply and args.target != uri:
        parser.error(f"--apply needs --target {uri} (the database this .env points to), to make the target explicit")

    from neo4j import GraphDatabase
    user = os.environ.get("NEO4J_USER") or os.environ.get("NEO4J_USERNAME") or "neo4j"
    driver = GraphDatabase.driver(uri, auth=(user, os.environ.get("NEO4J_PASSWORD", "")),
                                  notifications_min_severity="OFF")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    total_deleted = 0
    try:
        with driver.session(database=database) as session:
            version = neo4j_version(session)
            mode = "APPLY" if args.apply else ("CHECK QUERIES" if args.check_queries else "DRY RUN")
            log(f"Neo4J {uri} (database {database}, version {version}) - {mode}")
            if (args.apply or args.check_queries) and not supports_dynamic_types(version):
                log(f"STOP: Neo4j {version} has no dynamic relationship types (needs 5.26+); only the dry run works")
                return 1
            if args.check_queries:
                bad = 0
                for name, q in WRITE_QUERIES.items():
                    try:
                        session.run("EXPLAIN " + q, keep="x", dups=["y"]).consume()
                        log(f"  OK     {name}")
                    except Exception as exc:
                        bad += 1
                        log(f"  ERROR  {name}: {str(exc)[:300]}")
                return 1 if bad else 0

            for doc_name in args.doc:
                docs = session.run("MATCH (d:Document {name: $name}) RETURN elementId(d) AS eid, d.id AS id",
                                   name=doc_name).data()
                log(f"\n== {doc_name}: {len(docs)} Document node(s)")
                if not docs:
                    log("  not found (the name must match exactly)")
                    continue
                if len(docs) > 1:
                    log("  NOTE: more than one Document node with this name (a duplicate document); "
                        "each is handled on its own, copies across them are not merged")
                for doc in docs:
                    t = time.time()
                    rows = session.run(SECTIONS, doc=doc["eid"]).data()
                    groups = plan_groups(rows)
                    report(doc_name, doc, rows, groups, args.show)
                    log(f"    (read in {time.time() - t:.0f} s)")
                    if not args.apply or not groups:
                        continue
                    todo = groups[:args.limit_groups] if args.limit_groups else groups
                    deleted = 0
                    for n, g in enumerate(todo, 1):
                        dups = [d["eid"] for d in g["dups"]]

                        def _merge(tx, keep=g["keep"]["eid"], dups=dups):
                            for q in WRITE_QUERIES.values():
                                tx.run(q, keep=keep, dups=dups).consume()

                        session.execute_write(_merge)
                        deleted += len(dups)
                        if n % 200 == 0 or n == len(todo):
                            log(f"    applied {n}/{len(todo)} groups, {deleted} copies deleted")
                    total_deleted += deleted
    finally:
        driver.close()
    if args.apply:
        log(f"\nDone: {total_deleted} copies deleted. Run again without --apply: it should find 0 groups.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
