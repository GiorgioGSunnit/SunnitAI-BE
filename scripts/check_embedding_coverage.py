#!/usr/bin/env python3
"""
Embedding coverage check: how many nodes have a vector, old (`embedding`) and new (`embedding_v2`).

READ-ONLY: only MATCH / RETURN / SHOW queries. Uses existence checks, so it never reads the
vectors themselves and stays fast on the full graph.

Use it:
  - now, to see whether the new BE code (commit 5384b0e) has already written anything;
  - at step 9 of the fix plan, to confirm the backfill covered everything (--strict).

Usage (on the server, inside the BE virtualenv):
    cd /opt/sunnitai-be
    venv/bin/python scripts/check_embedding_coverage.py [--labels Section,CommentatoSection] [--strict]

Options:
    --labels    comma-separated labels (default: every label with a vector index in V2's searches)
    --strict    exit with code 1 if any Section / CommentatoSection / FiscalitaSection with text
                has no embedding_v2 (for step 9)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent          # scripts/
_ROOT = _SCRIPT_DIR.parent                             # project root

from dotenv import load_dotenv

# Load .env from project root (server: /opt/sunnitai-be/.env)
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=False)      # local dev override

DEFAULT_LABELS = ["Section", "CommentatoSection", "FiscalitaSection", "CcnlSection",
                  "Document", "LegalAct", "Penalty", "CourtCase"]

# What counts as "has text" per label: the backfill's rule (nodes without text can't be embedded).
HAS_TEXT = "(trim(coalesce(n.plain_text, '')) <> '' OR trim(coalesce(n.abstract, '')) <> '')"
TEXT_EXPR = {label: HAS_TEXT for label in ("Section", "CommentatoSection", "FiscalitaSection")}
TAG = re.compile(r"^\W*(\[[^\]]+\])")          # [PENALE], [CCNL]… (\W also skips a leading BOM)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only embedding coverage check (old vs new vectors).")
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    parser.add_argument("--strict", action="store_true", help="exit 1 if any node with text lacks embedding_v2")
    args = parser.parse_args()
    labels = [l.strip() for l in args.labels.split(",") if l.strip()]

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
        notifications_min_severity="OFF",
    )
    missing_total = 0
    print(f"Neo4J: {os.environ['NEO4J_URI']}\n")
    with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:

        # 1. Per label
        print("== 1. Vectors per label ==")
        print(f"  {'label':<20}{'nodes':>9}{'with text':>11}{'embedding':>11}{'embedding_v2':>14}{'text, no v2':>13}")
        for label in labels:
            has_text = TEXT_EXPR.get(label, "true")
            r = session.run(
                f"""
                MATCH (n:`{label}`)
                RETURN count(n) AS nodes,
                       count(CASE WHEN {has_text} THEN 1 END) AS with_text,
                       count(CASE WHEN n.embedding IS NOT NULL THEN 1 END) AS old,
                       count(CASE WHEN n.embedding_v2 IS NOT NULL THEN 1 END) AS new,
                       count(CASE WHEN {has_text} AND n.embedding_v2 IS NULL THEN 1 END) AS missing
                """
            ).single()
            # Only the labels the backfill embeds count as missing (Document etc. have no v2 plan).
            embeddable = label in TEXT_EXPR
            if embeddable:
                missing_total += r["missing"]
            with_text, missing = (r["with_text"], r["missing"]) if embeddable else ("-", "-")
            print(f"  {label:<20}{r['nodes']:>9}{with_text:>11}{r['old']:>11}{r['new']:>14}{missing:>13}")

        # 2. embedding_version values
        print("\n== 2. embedding_version on Sections ==")
        rows = session.run(
            "MATCH (n:Section) WHERE n.embedding_version IS NOT NULL "
            "RETURN n.embedding_version AS version, count(*) AS nodes ORDER BY nodes DESC"
        ).data()
        for r in rows:
            print(f"  {r['version']!s:<30}{r['nodes']:>9}")
        if not rows:
            print("  (none set)")

        # 3. Sections per document family
        print("\n== 3. Sections per document family (name tag) ==")
        rows = session.run("""
            MATCH (d:Document)-[:CONTAINS]->(s:Section)
            RETURN d.name AS name,
                   count(s) AS sections,
                   count(CASE WHEN s.embedding IS NOT NULL THEN 1 END) AS old,
                   count(CASE WHEN s.embedding_v2 IS NOT NULL THEN 1 END) AS new""").data()
        fam = defaultdict(lambda: [0, 0, 0, 0])     # documents, sections, old, new
        for r in rows:
            m = TAG.match(r["name"] or "")
            f = fam[m.group(1) if m else "(no tag)"]
            f[0] += 1
            f[1] += r["sections"]
            f[2] += r["old"]
            f[3] += r["new"]
        print(f"  {'family':<18}{'documents':>10}{'sections':>10}{'embedding':>11}{'embedding_v2':>14}")
        for tag, (docs, secs, old, new) in sorted(fam.items(), key=lambda kv: -kv[1][1]):
            print(f"  {tag:<18}{docs:>10}{secs:>10}{old:>11}{new:>14}")

        # 4. Documents that already have new vectors (written by the new code)
        print("\n== 4. Documents with embedding_v2 (first 15) ==")
        rows = session.run("""
            MATCH (d:Document)-[:CONTAINS]->(s:Section) WHERE s.embedding_v2 IS NOT NULL
            RETURN d.name AS name, count(s) AS sections ORDER BY name LIMIT 15""").data()
        for r in rows:
            print(f"  {r['sections']:>5} sections  {r['name']!r}")
        if not rows:
            print("  none: the new code hasn't written any vector to this database yet")

        # 5. Text properties of the labels whose writer is outside both repos
        print("\n== 5. Properties of one node per label (to know what text to embed) ==")
        for label in labels:
            rows = session.run(f"MATCH (n:`{label}`) RETURN keys(n) AS keys LIMIT 1").data()
            if rows:
                print(f"  {label:<20}{sorted(k for k in rows[0]['keys'] if 'embedding' not in k)}")

        # 6. Vector indexes and their state
        print("\n== 6. Vector indexes ==")
        try:
            rows = session.run(
                "SHOW INDEXES YIELD name, type, state, populationPercent, labelsOrTypes, properties "
                "WHERE type = 'VECTOR' RETURN name, state, populationPercent, labelsOrTypes, properties "
                "ORDER BY name"
            ).data()
            for r in rows:
                print(f"  {r['name']:<34}{r['state']:<12}{r['populationPercent']:>6.1f}%  "
                      f"{r['labelsOrTypes']}.{r['properties']}")
        except Exception as exc:
            print(f"  SHOW INDEXES failed: {exc}")

    driver.close()
    if args.strict and missing_total:
        print(f"\nSTRICT: {missing_total} nodes with text have no embedding_v2")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
