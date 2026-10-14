#!/usr/bin/env python3
"""
Retrieval A/B on the real index: for each test question, where does the expected article land
with today's search and with the fix?

READ-ONLY: Neo4J is only read (MATCH and vector index queries) and the embedding server only
embeds the questions. No chatbot involved and no setting changed, so it runs against any
database, before or after the switch.

It reproduces V2's vector search for Sections (SunnitAI-V2 src/rag/lookups.py, vector_lookup):
same index, k, filter and min_score.
  old    question sent as token IDs (LangChain defaults), index section_embeddings, min 0.25
  pool   the old search, counting only sections that already have embedding_v2: the same
         candidates as "new", so the two compare fairly while the backfill is partial
  new    plain text with the Qwen query instruction, index section_embeddings_v2, min 0.55

This measures vector search only. The chatbot also runs keyword search (BM25), routing and the
article cross-references, so a miss here doesn't necessarily mean a wrong answer.

Cases file (default: retrieval_cases.txt next to this script), one case per line:
    question | document | article[, article...]
  document   part of the Document name, case-insensitive (e.g. Codice Civile 2026)
  article    a number, optionally with bis/ter/... (e.g. 589-bis); any listed article is a hit
  Lines starting with # are skipped.

Usage (with the BE or V2 virtualenv; reads .env from the folder above scripts/):
    venv/bin/python scripts/compare_retrieval.py [--cases FILE] [--k 30] [--top 3] [--out FILE.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent          # scripts/
_ROOT = _SCRIPT_DIR.parent                             # project root

from dotenv import load_dotenv

# Load .env from project root (server: /opt/sunnitai-be/.env)
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=False)      # local dev override

QUERY_INSTRUCTION = (                                  # V2 embedding_config.QUERY_INSTRUCTION
    "Instruct: Given a legal question in Italian, retrieve the passages of laws, rulings and "
    "commentary that answer it\nQuery: "
)
TRUNCATE_TOKENS = 8000
POOL_SCAN = 1000           # old-index candidates scanned to find the top k that have embedding_v2
SUFFIXES = "bis|ter|quater|quinquies|sexies|septies|octies|novies|decies"
MODES = ("old", "pool", "new")


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ── Cases ──────────────────────────────────────────────────────────────────────

def article_pattern(article: str) -> re.Pattern:
    """1434 matches 'Art. 1434' and 'Art. 1434_2', not 'Art. 14340' or 'Art. 1434-bis'."""
    m = re.fullmatch(r"(\d+)\s*[-.]?\s*([a-z]*)", article.strip().lower())
    if not m:
        raise ValueError(f"not an article number: {article!r}")
    num, suffix = m.groups()
    if suffix:
        return re.compile(rf"(?<!\d){num}\s*[-.]?\s*{suffix}\b", re.I)
    return re.compile(rf"(?<!\d){num}(?!\d)(?!\s*[-.]?\s*(?:{SUFFIXES})\b)", re.I)


def load_cases(path: Path) -> list[dict]:
    cases = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not all(parts):
            sys.exit(f"{path}:{n}: expected 'question | document | articles', got {line!r}")
        question, document, articles = parts
        articles = [a.strip() for a in articles.split(",") if a.strip()]
        try:
            patterns = [article_pattern(a) for a in articles]
        except ValueError as exc:
            sys.exit(f"{path}:{n}: {exc}")
        cases.append({"line": n, "question": question, "document": document,
                      "articles": articles, "patterns": patterns})
    return cases


# ── Clients ────────────────────────────────────────────────────────────────────

def _env(*names: str, default: str = "") -> str:
    for name in names:
        if os.getenv(name):
            return os.environ[name]
    return default


def embedders():
    """The question clients of V2's two modes (ai_chat._init_openai_embeddings). Accepts BE and
    V2 variable names; LLM_BASE_URL comes last because in V2's .env it is the chat server."""
    from langchain_openai import OpenAIEmbeddings
    model = _env("LLM_EMBEDDING_MODEL", "EMBEDDING_MODEL")
    url = _env("LLM_EMBEDDING_BASE_URL", "EMBEDDING_BASE_URL", "LLM_BASE_URL")
    key = _env("LLM_EMBEDDING_API_KEY", "EMBEDDING_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY",
               default="EMPTY")
    common = {"model": model, "api_key": key, "base_url": url or None, "timeout": 60}
    old = OpenAIEmbeddings(**common)                   # defaults: cl100k token IDs
    new = OpenAIEmbeddings(**common, check_embedding_ctx_length=False,
                           model_kwargs={"extra_body": {"truncate_prompt_tokens": TRUNCATE_TOKENS}})
    return model, url, old, new


# ── Queries ────────────────────────────────────────────────────────────────────

def search(session, index: str, embedding: list, k: int, pool: bool = False) -> list[dict]:
    """V2's Section filter: sections without plain_text are dropped from vector results."""
    query = ("CALL db.index.vector.queryNodes($index, $scan, $embedding) YIELD node, score "
             "WHERE coalesce(node.plain_text, '') <> '' "
             + ("AND node.embedding_v2 IS NOT NULL " if pool else "")
             + "RETURN elementId(node) AS eid, score ORDER BY score DESC LIMIT $k")
    return session.run(query, index=index, scan=POOL_SCAN if pool else k,
                       embedding=embedding, k=k).data()


def doc_sections(session, document: str, cache: dict) -> list[dict]:
    key = document.lower()
    if key not in cache:
        cache[key] = session.run(
            "MATCH (d:Document)-[:CONTAINS]->(s:Section) WHERE toLower(d.name) CONTAINS $doc "
            "RETURN elementId(s) AS eid, d.name AS doc, coalesce(s.name, '') AS name, "
            "       s.embedding_v2 IS NOT NULL AS has_v2, "
            "       coalesce(s.plain_text, '') <> '' AS has_text",
            doc=key).data()
    return cache[key]


def names(session, eids: list[str], cache: dict) -> None:
    todo = [e for e in eids if e not in cache]
    if todo:
        for r in session.run(
                "UNWIND $eids AS eid MATCH (s) WHERE elementId(s) = eid "
                "OPTIONAL MATCH (d:Document)-[:CONTAINS]->(s) "
                "RETURN eid, coalesce(d.name, '?') AS doc, coalesce(s.name, '?') AS name",
                eids=todo):
            cache[r["eid"]] = f"{r['doc']} / {r['name']}"


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only retrieval A/B: old vs new vector search.")
    parser.add_argument("--cases", default=str(_SCRIPT_DIR / "retrieval_cases.txt"))
    parser.add_argument("--k", type=int, default=30, help="results per search (V2 uses 30)")
    parser.add_argument("--top", type=int, default=3, help="top hits printed per case (old and new)")
    parser.add_argument("--old-index", default="section_embeddings")
    parser.add_argument("--new-index", default="section_embeddings_v2")
    parser.add_argument("--old-min", type=float, default=0.25, help="V2's min_score today")
    parser.add_argument("--new-min", type=float, default=0.55, help="V2's text-mode min_score")
    parser.add_argument("--no-pool", action="store_true", help="skip the same-candidates comparison")
    parser.add_argument("--out", default=None, help="write per-case results as JSON")
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    if not cases:
        sys.exit(f"no cases in {args.cases}")
    t_start = time.time()

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(_env("NEO4J_USER", "NEO4J_USERNAME", default="neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
        notifications_min_severity="OFF",
    )
    model, url, old_client, new_client = embedders()
    log(f"Neo4J: {os.environ['NEO4J_URI']}")
    log(f"Embeddings: {model} at {url}")
    log(f"Cases: {len(cases)} from {args.cases}")

    with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
        states = {r["name"]: f"{r['state']} {r['populationPercent']:.0f}%" for r in session.run(
            "SHOW INDEXES YIELD name, state, populationPercent WHERE name IN $names "
            "RETURN name, state, populationPercent", names=[args.old_index, args.new_index])}
        counts = session.run(
            "MATCH (s:Section) WHERE coalesce(s.plain_text, '') <> '' "
            "RETURN count(s) AS text, count(CASE WHEN s.embedding_v2 IS NOT NULL THEN 1 END) AS v2"
        ).single()
        log(f"Indexes: {args.old_index} {states.get(args.old_index, 'MISSING')}, "
            f"{args.new_index} {states.get(args.new_index, 'MISSING')}")
        log(f"Sections with plain_text: {counts['text']}, of which with embedding_v2: {counts['v2']}")

        modes = ["old"]
        if args.new_index in states:
            partial = counts["v2"] < counts["text"]
            if partial and not args.no_pool:
                modes.append("pool")
            modes.append("new")
            if partial:
                log("  backfill partial: 'new' only sees sections with embedding_v2; compare it with 'pool'")
        else:
            log(f"  {args.new_index} missing: only today's search is measured")

        cache_docs: dict = {}
        cache_names: dict = {}
        results = []
        for i, case in enumerate(cases, 1):
            log(f"\n[{i:>2}] {case['question']}")
            sections = doc_sections(session, case["document"], cache_docs)
            expected = [s for s in sections if any(p.search(s["name"]) for p in case["patterns"])]
            row = {"line": case["line"], "question": case["question"], "document": case["document"],
                   "articles": case["articles"], "expected_sections": len(expected)}
            what = f"{case['document']} / art. {', '.join(case['articles'])}"
            if not sections:
                log(f"     expected  {what}: NO DOCUMENT matches {case['document']!r} (case skipped)")
                row["skipped"] = "document not found"
                results.append(row)
                continue
            if not expected:
                examples = ", ".join(repr(s["name"]) for s in sections[:5])
                log(f"     expected  {what}: NOT FOUND among {len(sections)} sections (case skipped)")
                log(f"               section names look like: {examples}")
                row["skipped"] = "article not found"
                results.append(row)
                continue
            with_v2 = sum(s["has_v2"] for s in expected)
            no_text = sum(not s["has_text"] for s in expected)
            note = f", {with_v2} with embedding_v2" if "new" in modes else ""
            note += f", {no_text} without plain_text (never returned)" if no_text else ""
            log(f"     expected  {what}  ({len(expected)} sections{note})")
            wanted = {s["eid"] for s in expected}

            embeddings = {}
            for mode, client, text in (("old", old_client, case["question"]),
                                       ("new", new_client, QUERY_INSTRUCTION + case["question"])):
                if mode in modes:
                    try:
                        embeddings[mode] = client.embed_query(text)
                    except Exception as exc:
                        log(f"     {mode:<5} embedding failed: {str(exc)[:150]}")
            for mode in modes:
                emb = embeddings.get("new" if mode == "new" else "old")
                if emb is None:
                    continue
                index = args.new_index if mode == "new" else args.old_index
                min_score = args.new_min if mode == "new" else args.old_min
                try:
                    hits = search(session, index, emb, args.k, pool=(mode == "pool"))
                except Exception as exc:
                    log(f"     {mode:<5} search failed: {str(exc)[:150]}")
                    continue
                rank = next((n for n, h in enumerate(hits, 1) if h["eid"] in wanted), None)
                score = hits[rank - 1]["score"] if rank else None
                kept = bool(rank) and score >= min_score
                row[mode] = {"rank": rank, "score": None if score is None else round(score, 4), "kept": kept}
                found = (f"rank {rank:>2}  score {score:.3f}  {'kept' if kept else f'below {min_score}'}"
                         if rank else f"not in top {args.k}")
                log(f"     {mode:<5} {found}")
                if mode != "pool" and args.top:
                    shown = hits[:args.top]
                    names(session, [h["eid"] for h in shown], cache_names)
                    for n, h in enumerate(shown, 1):
                        mark = ">" if h["eid"] in wanted else " "
                        log(f"           {mark}{n}. {h['score']:.3f}  {cache_names.get(h['eid'], h['eid'])}")
            results.append(row)

    driver.close()

    # Summary
    scored = [r for r in results if "skipped" not in r]
    log(f"\n== Summary: {len(scored)} cases scored, {len(results) - len(scored)} skipped ==")
    ks = [k for k in (1, 5, 10) if k < args.k] + [args.k]
    log(f"  {'mode':<6}" + "".join(f"{'@' + str(k):>8}" for k in ks) + f"{'kept':>8}")
    for mode in modes:
        rows = [r.get(mode) or {"rank": None, "kept": False} for r in scored]
        cells = [f"{sum(1 for x in rows if x['rank'] and x['rank'] <= k)}/{len(rows)}" for k in ks]
        cells.append(f"{sum(1 for x in rows if x['kept'])}/{len(rows)}")
        log(f"  {mode:<6}" + "".join(f"{c:>8}" for c in cells))
    log(f"  @k = expected article in the top k; kept = in the top {args.k} and above the mode's "
        f"min_score ({args.old_min} old, {args.new_min} new), i.e. what V2 passes on")
    for r in results:
        if "skipped" in r:
            log(f"  skipped line {r['line']}: {r['skipped']}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"\nPer-case results written to {args.out}")
    log(f"\nDone in {time.time() - t_start:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
