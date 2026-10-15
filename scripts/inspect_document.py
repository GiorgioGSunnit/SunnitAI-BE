#!/usr/bin/env python3
"""
Read-only look at how one document sits in the graph: its Document node(s), how its
Section names are formed, which kind of ids they have, duplicates, and the text of
chosen articles. Use it before and after a re-ingest, or to diagnose a bad parse.

READ-ONLY: only MATCH / RETURN.

Usage (BE or V2 virtualenv; .env from the folder above scripts/):
    venv/bin/python scripts/inspect_document.py --doc "Codice degli Appalti 2026" --article 93 --article 94
    venv/bin/python scripts/inspect_document.py --doc "Legge sul divorzio 2024" --like divorzio
    venv/bin/python scripts/inspect_document.py --doc "Codice degli Appalti 2026" --export /tmp/appalti_before.jsonl

  --like      also list every Document whose name contains this text (case-insensitive)
  --article   print the sections of this article number (name, id kind, first 300 chars); repeatable
  --export    write every section (name, id, abstract, plain_text) as JSON lines: a copy to keep
              before deleting and re-ingesting the document
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=False)


def log(msg: str = "") -> None:
    print(msg, flush=True)


def id_kind(node_id: str | None) -> str:
    tail = (node_id or "").rsplit("::", 1)[-1]
    if re.fullmatch(r"-?\d+", tail):
        return "python-hash"
    if re.fullmatch(r"[0-9a-f]{20}", tail):
        return "hex"
    return "other"


def name_shape(name: str) -> str:
    """The form of a section name with the numbers replaced: '584.0.0' -> 'N.N.N', 'vigore_dal.3' -> 'vigore_dal.N'."""
    return re.sub(r"\d+", "N", name or "(empty)")


def article_of(name: str) -> str | None:
    m = re.search(r"(?:^|\.)(\d+(?:-?(?:bis|ter|quater|quinquies|sexies|septies|octies|novies|decies))?)(?=$|[._])",
                  name or "", re.IGNORECASE)
    return m.group(1).lower().replace("-", "") if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only view of one document in the graph.")
    parser.add_argument("--doc", required=True, help="exact Document name")
    parser.add_argument("--like", default=None, help="also list Documents whose name contains this")
    parser.add_argument("--article", action="append", default=[], help="article number to print (repeatable)")
    parser.add_argument("--export", default=None, help="write every section as JSON lines to this file")
    args = parser.parse_args()

    from neo4j import GraphDatabase
    uri = os.environ["NEO4J_URI"]
    user = os.environ.get("NEO4J_USER") or os.environ.get("NEO4J_USERNAME") or "neo4j"
    driver = GraphDatabase.driver(uri, auth=(user, os.environ.get("NEO4J_PASSWORD", "")),
                                  notifications_min_severity="OFF")
    log(f"Neo4J: {uri}")
    try:
        with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
            if args.like:
                rows = session.run(
                    "MATCH (d:Document) WHERE toLower(d.name) CONTAINS toLower($like) "
                    "RETURN d.name AS name, d.id AS id, d.file_name AS file, COUNT { (d)-[:CONTAINS]->() } AS sections "
                    "ORDER BY name", like=args.like).data()
                log(f"\n== Documents whose name contains {args.like!r}: {len(rows)}")
                for r in rows:
                    log(f"  {r['sections']:>6} sections  {r['name']!r}  {r['id']}  file={r['file']!r}")

            docs = session.run("MATCH (d:Document {name: $name}) RETURN elementId(d) AS eid, d.id AS id, "
                               "d.hash AS hash, d.file_name AS file", name=args.doc).data()
            log(f"\n== {args.doc!r}: {len(docs)} Document node(s)")
            out = open(args.export, "w", encoding="utf-8") if args.export else None
            for doc in docs:
                secs = session.run(
                    "MATCH (d:Document)-[:CONTAINS]->(s) WHERE elementId(d) = $eid "
                    "RETURN s.id AS id, labels(s) AS labels, coalesce(s.name, '') AS name, "
                    "coalesce(s.abstract, '') AS abstract, coalesce(s.plain_text, '') AS text, "
                    "s.embedding_v2 IS NOT NULL AS v2", eid=doc["eid"]).data()
                log(f"\n  Document {doc['id']}  file={doc['file']!r}  sections={len(secs)}")
                stale = session.run(
                    "MATCH (s:DOCUMENT_SECTION) WHERE s.id STARTS WITH $p RETURN count(s) AS n",
                    p=f"DOCUMENT_SECTION::{doc['hash']}::").single()["n"] if doc["hash"] else 0
                labels = Counter("/".join(sorted(s["labels"])) for s in secs)
                log(f"    labels: {dict(labels)}; never-relabelled DOCUMENT_SECTION nodes with this hash: {stale}")
                log(f"    id kinds: {dict(Counter(id_kind(s['id']) for s in secs))}")
                log(f"    with embedding_v2: {sum(s['v2'] for s in secs)}; without text: "
                    f"{sum(1 for s in secs if not s['text'].strip())}")
                copies = Counter((s["name"], " ".join(s["text"].split())) for s in secs)
                log(f"    duplicate copies (same name and text): {sum(n - 1 for n in copies.values() if n > 1)}")
                log("    name shapes (most common):")
                for shape, n in Counter(name_shape(s["name"]) for s in secs).most_common(12):
                    log(f"      {n:>6}  {shape}")
                wanted = {a.lower().replace("-", "") for a in args.article}
                for s in sorted(secs, key=lambda s: s["name"]):
                    if wanted and article_of(s["name"]) in wanted:
                        text = " ".join(s["text"].split())
                        log(f"    --- {s['name']}  [{id_kind(s['id'])}]  {text[:300]}{'…' if len(text) > 300 else ''}")
                    if out:
                        out.write(json.dumps({"document": args.doc, "document_id": doc["id"], "id": s["id"],
                                              "name": s["name"], "abstract": s["abstract"], "plain_text": s["text"]},
                                             ensure_ascii=False) + "\n")
            if out:
                out.close()
                log(f"\nExported to {args.export}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
