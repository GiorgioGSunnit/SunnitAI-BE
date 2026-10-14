#!/usr/bin/env python3
"""
Embedding consistency check — which model wrote the Section vectors in Neo4J?

READ-ONLY: only MATCH / RETURN / SHOW queries, nothing is written to Neo4J.

Steps:
  1. Config       embedding model / endpoint BE is configured with, and how the
                  LangChain client sends text (OpenAI token IDs vs plain text)
  2. Server       which model the embedding endpoint actually serves
  3. Neo4J        Section vectors counted by size, and the vector indexes
  4. Fingerprint  samples stored Section vectors, re-embeds the same text with
                  every candidate below, prints cosine(stored, candidate)

Reading the fingerprint (rough guide — raw numbers are printed too):
    >= 0.995       MATCH     this candidate wrote the stored vector
    0.90 - 0.995   CLOSE     same model, something differs
                             (quantization, text recipe or input method)
    < 0.90         NO MATCH  different model

Candidates are <source>/<input method>/<text recipe>:
    be/langchain/*   BE endpoint through BE's own client (LangChain defaults:
                     text is tokenized with OpenAI's tokenizer, token IDs are sent)
    be/plain/*       same endpoint, raw text sent
    alt/...          same, for a second endpoint (--alt-base-url / --alt-model)
    bge/local/*      BAAI/bge-large-en-v1.5 run locally (--bge): BE in May 2026,
                     V2 with EMBEDDING_PROVIDER=local
  Text recipes:
    be         abstract + " " + plain_text                   (BE post_process step 3)
    v2         title + text_en + abstract + plain_text       (V2 embed_missing job)
    v2+prefix  v2 with the "Instruct: ..." line V2 adds when
               EMBEDDING_PROVIDER=openai and the model name contains "qwen"

Usage (on the server, inside the virtualenv):
    source /opt/sunnitai-be/venv/bin/activate
    cd /opt/sunnitai-be
    python scripts/check_embeddings.py [--samples 5] [--quick] [--doc HASH_OR_NAME] [--list-docs] [--bge]
                                       [--alt-base-url URL --alt-model NAME [--alt-api-key KEY]]

Options:
    --samples N       Sections to sample per vector size (default 5)
    --quick           Estimate the vector-size breakdown from 2000 random sections
                      instead of reading every stored vector (use on large graphs)
    --doc X           Only sample documents whose hash, id or name contains X
    --list-docs       Print every document with the vector size(s) of its sections
                      (reads every stored vector, slow on large graphs)
    --bge             Also test BAAI/bge-large-en-v1.5 locally
                      (downloads ~1.3 GB on first use if not cached)
    --alt-base-url    Second OpenAI-compatible endpoint to test, e.g. V2's
    --alt-model       EMBEDDING_BASE_URL / EMBEDDING_MODEL, or an INT4 deployment
    --alt-api-key
"""
from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent          # scripts/
_ROOT = _SCRIPT_DIR.parent                             # project root
_SRC = _ROOT / "src" / "be" / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

# Load .env from project root (server: /opt/sunnitai-be/.env)
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=False)      # local dev override

from lex_package.utils.embeddings import cosine_similarity, embeddings_enabled


MATCH_AT = 0.995
CLOSE_AT = 0.90
REQUEST_TIMEOUT = 60   # seconds per embedding call (openai default is 600, with retries)
POOL_SIZE = 2000       # random sections picked once; samples per vector size come from here

# Must stay identical to V2 src/preprocessing/generate_embeddings.py::_add_doc_prefix
V2_QWEN_PREFIX = "Instruct: Represent this Italian legal document section for retrieval\n"

_QUANT_HINTS = ("fp8", "awq", "gptq", "int4", "int8", "w4a16", "w8a8", "w8a16", "bnb", "gguf", "nf4")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _header(title: str) -> None:
    print(f"\n{'═' * 78}\n{title}\n{'═' * 78}")


def _pkg_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def _verdict(score: float | None) -> str:
    if score is None:
        return "-"
    if score >= MATCH_AT:
        return "MATCH"
    if score >= CLOSE_AT:
        return "CLOSE"
    return "NO MATCH"


def _compare(stored: list[float], candidate: list[float]) -> tuple[float | None, str]:
    """Cosine between stored and candidate. Longer candidates are cut to the
    stored size (Qwen3 embeddings stay valid when truncated)."""
    if not candidate:
        return None, "empty"
    if len(candidate) == len(stored):
        return cosine_similarity(stored, candidate), ""
    if len(candidate) > len(stored):
        return cosine_similarity(stored, candidate[: len(stored)]), "cut"
    return None, f"dim {len(candidate)}"


# ── Endpoints ──────────────────────────────────────────────────────────────────

def _be_endpoint() -> dict:
    """Same lookup order as lex_package.llm.factory.build_embedding_model."""
    emb_url = os.getenv("LLM_EMBEDDING_BASE_URL") or ""
    return {
        "name": "be",
        "model": os.getenv("LLM_EMBEDDING_MODEL") or "",
        "base_url": emb_url or os.getenv("LLM_BASE_URL") or "",
        "api_key": (os.getenv("LLM_EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY")
                    or os.getenv("OPENAI_API_KEY") or "EMPTY"),
        "url_from_fallback": not emb_url,
    }


def _langchain_client(ep: dict):
    """Same arguments as lex_package.llm.factory.build_embedding_model (so the same
    tokenization / input method), plus a timeout so a busy server can't stall the check."""
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model=ep["model"], api_key=ep["api_key"], base_url=ep["base_url"] or None,
        timeout=REQUEST_TIMEOUT, max_retries=1,
    )


def _plain_client(ep: dict):
    from openai import OpenAI
    return OpenAI(base_url=ep["base_url"] or None, api_key=ep["api_key"],
                  timeout=REQUEST_TIMEOUT, max_retries=1)


def _embed_plain(client, model: str, text: str) -> list[float]:
    resp = client.embeddings.create(model=model, input=[text], encoding_format="float")
    return [float(x) for x in resp.data[0].embedding]


# ── 1. Config ──────────────────────────────────────────────────────────────────

def report_config(be: dict) -> None:
    _header("1. BE embedding config (from .env)")
    print(f"  LLM_EMBEDDING_MODEL     : {be['model'] or '(NOT SET)'}")
    url_note = "  ← LLM_EMBEDDING_BASE_URL empty, falling back to LLM_BASE_URL (chat server!)" \
        if be["url_from_fallback"] else ""
    print(f"  embedding base URL      : {be['base_url'] or '(NOT SET)'}{url_note}")
    print(f"  API key set             : {'yes' if be['api_key'] != 'EMPTY' else 'no'}")
    print(f"  langchain-openai        : {_pkg_version('langchain-openai')}")
    print(f"  features.json flag      : embeddings_enabled={embeddings_enabled()} "
          "(post_process step 3 ignores it)")
    try:
        from lex_package.llm.factory import build_embedding_model
        client = build_embedding_model(target="primary")   # exactly what BE uses
        tik = getattr(client, "tiktoken_enabled", None)
        chk = getattr(client, "check_embedding_ctx_length", None)
        print(f"  client tiktoken_enabled : {tik}")
        print(f"  client check_ctx_length : {chk}")
        print(f"  client dimensions       : {getattr(client, 'dimensions', None)}")
        if tik and chk:
            print("  → BE sends OpenAI (cl100k) token IDs to the server, not the text itself.")
        elif chk:
            print("  → BE re-tokenizes with the HuggingFace tokenizer and sends text chunks.")
        else:
            print("  → BE sends plain text.")
    except Exception as exc:
        print(f"  could not build BE client: {exc}")


# ── 2. Server ──────────────────────────────────────────────────────────────────

def report_server(ep: dict) -> None:
    _header(f"2. Embedding server [{ep['name']}] {ep['base_url']}")
    if not ep["base_url"]:
        print("  no base URL — skipped")
        return
    try:
        client = _plain_client(ep)
        served = [m.model_dump() for m in client.models.list().data]
    except Exception as exc:
        print(f"  /models failed: {exc}")
        return
    for m in served:
        name = f"{m.get('id')} {m.get('root') or ''}".lower()
        hints = [h for h in _QUANT_HINTS if h in name]
        print(f"  served model : id={m.get('id')}  root={m.get('root')}  "
              f"max_model_len={m.get('max_model_len')}")
        print(f"  quantization : {', '.join(hints) if hints else 'not visible in the name — check the server launch flags'}")
    if ep["model"] and ep["model"] not in {m.get("id") for m in served}:
        print(f"  ⚠ configured model '{ep['model']}' is NOT among the served models")
    try:
        dim = len(_embed_plain(client, ep["model"], "prova"))
        print(f"  vector size  : {dim} (default, no 'dimensions' requested)")
    except Exception as exc:
        print(f"  test embedding failed: {exc}")


# ── 3. Neo4J ───────────────────────────────────────────────────────────────────

_DIM_EXPR = "CASE WHEN s.embedding IS NULL THEN 0 ELSE size(s.embedding) END"


def _doc_filter(doc: str | None) -> str:
    return "AND (d.hash CONTAINS $doc OR d.id CONTAINS $doc OR d.name CONTAINS $doc)" if doc else ""


def sample_pool(session, size: int, doc: str | None) -> list[dict]:
    """Random Sections that have a vector, with their vector size. Picks the nodes
    first and only reads the vectors of the picked ones (no full vector scan)."""
    return session.run(
        f"""
        MATCH (d:Document)-[:CONTAINS]->(s:Section)
        WHERE s.embedding IS NOT NULL {_doc_filter(doc)}
        WITH s ORDER BY rand() LIMIT $size
        RETURN elementId(s) AS eid, size(s.embedding) AS dim
        """,
        size=size, doc=doc,
    ).data()


def report_neo4j_quick(session, pool: list[dict], list_docs: bool) -> list[int]:
    _header("3. Neo4J — Section vectors by size (--quick: estimated from a sample)")
    with_vec = "count(CASE WHEN s.embedding IS NOT NULL THEN 1 END)"
    r = session.run(
        f"MATCH (d:Document)-[:CONTAINS]->(s:Section) "
        f"RETURN count(DISTINCT d) AS documents, count(s) AS sections, {with_vec} AS with_vector"
    ).single()
    print(f"  Sections linked to a Document: {r['sections']} in {r['documents']} documents, "
          f"{r['with_vector']} with a vector, {r['sections'] - r['with_vector']} without")
    r = session.run(f"MATCH (s:Section) RETURN count(s) AS sections, {with_vec} AS with_vector").single()
    print(f"  All Section nodes:             {r['sections']}, {r['with_vector']} with a vector")

    counts: dict[int, int] = {}
    for p in pool:
        counts[p["dim"]] = counts.get(p["dim"], 0) + 1
    print(f"  Vector sizes in {len(pool)} random sections with a vector:")
    for dim, k in sorted(counts.items()):
        print(f"    dim {dim:>5} : {k:>5}  ({k / len(pool):.1%})")
    _report_vector_indexes(session)
    if list_docs:
        _report_docs(session)
    return [d for d in sorted(counts) if d > 0]


def report_neo4j(session, list_docs: bool) -> list[int]:
    _header("3. Neo4J — Section vectors by size (0 = no vector)")
    print("  (reads every stored vector — can take a few minutes on a large graph; --quick avoids it)")

    print("  Sections linked to a Document (BE ingestion):")
    rows = session.run(
        f"""
        MATCH (d:Document)-[:CONTAINS]->(s:Section)
        WITH d, s, {_DIM_EXPR} AS dim
        RETURN dim, count(DISTINCT d) AS documents, count(s) AS sections
        ORDER BY dim
        """
    ).data()
    for r in rows:
        print(f"    dim {r['dim']:>5} : {r['sections']:>7} sections in {r['documents']:>5} documents")

    print("  All Section nodes (includes sections not linked to a Document):")
    for r in session.run(
        f"MATCH (s:Section) WITH {_DIM_EXPR} AS dim RETURN dim, count(*) AS sections ORDER BY dim"
    ).data():
        print(f"    dim {r['dim']:>5} : {r['sections']:>7} sections")

    _report_vector_indexes(session)
    if list_docs:
        _report_docs(session)
    return [r["dim"] for r in rows if r["dim"] > 0]


def _report_vector_indexes(session) -> None:
    print("  Vector indexes:")
    try:
        idx = session.run(
            "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, options "
            "WHERE type = 'VECTOR' RETURN name, labelsOrTypes, properties, options"
        ).data()
        for r in idx:
            cfg = (r.get("options") or {}).get("indexConfig") or {}
            print(f"    {r['name']:<34} {r['labelsOrTypes']}.{r['properties']}  "
                  f"dims={cfg.get('vector.dimensions')}  sim={cfg.get('vector.similarity_function')}")
        if not idx:
            print("    (none)")
    except Exception as exc:
        print(f"    SHOW INDEXES failed: {exc}")


def _report_docs(session) -> None:
    print("  Documents (dim:sections) — reads every stored vector:")
    for r in session.run(
        f"""
        MATCH (d:Document)-[:CONTAINS]->(s:Section)
        WITH d, {_DIM_EXPR} AS dim
        WITH d, dim, count(*) AS n ORDER BY dim
        RETURN coalesce(d.name, d.title, d.id) AS doc, d.hash AS hash,
               collect(toString(dim) + ':' + toString(n)) AS dims
        ORDER BY doc
        """
    ).data():
        print(f"    {str(r['hash'] or '')[:12]:<12}  {', '.join(r['dims']):<22}  {r['doc']}")


_SAMPLE_RETURN = """
        RETURN coalesce(d.name, d.title, d.id) AS doc, s.name AS section,
               s.embedding AS embedding,
               coalesce(s.abstract, "") + " " + coalesce(s.plain_text, "") AS text_be,
               toString(COALESCE(s.title, '')) + ' ' + toString(COALESCE(s.text_en, '')) + ' '
                 + toString(COALESCE(s.abstract, '')) + ' ' + toString(COALESCE(s.plain_text, '')) AS text_v2
"""


def sample_sections(session, dim: int, n: int, doc: str | None, pool: list[dict]) -> list[dict]:
    """n random Sections with vectors of this size: taken from the pool when it has
    enough, otherwise a targeted query (slower: it reads every vector to check its size)."""
    eids = [p["eid"] for p in pool if p["dim"] == dim][:n]
    if len(eids) >= n:
        return session.run(
            f"MATCH (d:Document)-[:CONTAINS]->(s:Section) WHERE elementId(s) IN $eids {_SAMPLE_RETURN}",
            eids=eids,
        ).data()
    return session.run(
        f"""
        MATCH (d:Document)-[:CONTAINS]->(s:Section)
        WHERE s.embedding IS NOT NULL AND size(s.embedding) = $dim {_doc_filter(doc)}
        WITH d, s ORDER BY rand() LIMIT $n
        {_SAMPLE_RETURN}
        """,
        dim=dim, n=n, doc=doc,
    ).data()


# ── 4. Fingerprint ─────────────────────────────────────────────────────────────

def _recipes(sample: dict, with_prefix: bool) -> dict[str, str]:
    # Same cleaning as each writer: BE strips, V2 strips and falls back to the label.
    v2 = sample["text_v2"].strip() or "Section"
    out = {"be": (sample["text_be"] or "").strip(), "v2": v2}
    if with_prefix:
        out["v2+prefix"] = V2_QWEN_PREFIX + v2
    return out


def build_candidates(endpoints: list[dict], use_bge: bool) -> list[tuple[str, callable, bool]]:
    """Returns (label prefix, embed(text) -> vector, qwen_prefix_applies)."""
    cands = []
    for ep in endpoints:
        if not (ep["base_url"] and ep["model"]):
            print(f"  [{ep['name']}] endpoint not configured — skipped")
            continue
        qwen = "qwen" in ep["model"].lower()
        try:
            lc = _langchain_client(ep)
            cands.append((f"{ep['name']}/langchain", lambda t, c=lc: [float(x) for x in c.embed_documents([t])[0]], qwen))
        except Exception as exc:
            print(f"  [{ep['name']}] LangChain client failed: {exc}")
        plain = _plain_client(ep)
        cands.append((f"{ep['name']}/plain", lambda t, c=plain, m=ep["model"]: _embed_plain(c, m, t), qwen))
    if use_bge:
        try:
            from sentence_transformers import SentenceTransformer
            print("  loading BAAI/bge-large-en-v1.5 …")
            st = SentenceTransformer("BAAI/bge-large-en-v1.5")
            cands.append(("bge/local", lambda t, m=st: [float(x) for x in m.encode([t], normalize_embeddings=True)[0]], False))
        except Exception as exc:
            print(f"  bge load failed: {exc}")
    return cands


def fingerprint(session, dims: list[int], cands, n: int, doc: str | None, pool: list[dict]) -> None:
    _header("4. Fingerprint — cosine(stored vector, re-embedded text)")
    if not cands:
        print("  no candidates to test")
        return

    for dim in dims:
        samples = sample_sections(session, dim, n, doc, pool)
        print(f"\n── Stored vectors with {dim} dims — {len(samples)} sample(s) ──")
        if not samples:
            continue
        for i, s in enumerate(samples, 1):
            print(f"  s{i}: {s['doc']} / section {s['section']}  ({len(s['text_be'])} chars)")

        results: dict[str, list[tuple[float | None, str]]] = {}
        vectors: dict[str, list[list[float]]] = {}
        errors: list[str] = []
        for prefix, embed, qwen in cands:
            print(f"  [progress] dim {dim}: embedding samples with {prefix} …", file=sys.stderr)
            for s_idx, s in enumerate(samples):
                for recipe, text in _recipes(s, qwen).items():
                    label = f"{prefix}/{recipe}"
                    try:
                        vec = embed(text)
                        score, note = _compare(s["embedding"], vec)
                    except Exception as exc:
                        vec, score, note = [], None, "error"
                        errors.append(f"{label} s{s_idx + 1}: {str(exc)[:160]}")
                    results.setdefault(label, []).append((score, note))
                    vectors.setdefault(label, []).append(vec)

        # Sections of the same size may come from different writers (BE step 3 vs
        # V2 embed_missing), so judge each sample on its own, not on averages.
        print(f"\n  {'candidate':<28}" + "".join(f"{'s' + str(i):>10}" for i in range(1, len(samples) + 1))
              + "   matches")
        for label, cells in results.items():
            row = "".join(
                f"{(f'{sc:.4f}' + ('*' if note == 'cut' else '')) if sc is not None else note:>10}"
                for sc, note in cells
            )
            hits = sum(1 for sc, _ in cells if sc is not None and sc >= MATCH_AT)
            print(f"  {label:<28}{row}   {hits}/{len(cells)}")

        print("\n  Best candidate per sample:")
        tally: dict[str, int] = {}
        for s_idx in range(len(samples)):
            scored = [(cells[s_idx][0], label) for label, cells in results.items() if cells[s_idx][0] is not None]
            if not scored:
                print(f"    s{s_idx + 1}: no comparable candidate")
                continue
            score = max(sc for sc, _ in scored)
            # Ties happen when recipes produce the same text (e.g. no title) — show them all.
            tied = sorted(lbl for sc, lbl in scored if sc >= score - 0.0005)
            label = " = ".join(tied[:3]) + (f" (+{len(tied) - 3})" if len(tied) > 3 else "")
            verdict = _verdict(score)
            key = label if verdict == "MATCH" else f"{label} ({verdict})"
            tally[key] = tally.get(key, 0) + 1
            print(f"    s{s_idx + 1}: {label:<28} {score:.4f}  {verdict}")
        print("  Summary: " + "; ".join(f"{k} × {v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1])))
        if any(note == "cut" for cells in results.values() for _, note in cells):
            print("  * candidate was longer than the stored vector; compared on the first "
                  f"{dim} values")

        # Same text, same endpoint, two input methods: isolates the token-ID effect.
        for ep_name in ("be", "alt"):
            a, b = vectors.get(f"{ep_name}/langchain/be"), vectors.get(f"{ep_name}/plain/be")
            if a and b:
                sims = [f"{cosine_similarity(x, y):.4f}" for x, y in zip(a, b) if x and y and len(x) == len(y)]
                if sims:
                    print(f"  [{ep_name}] same text, LangChain token IDs vs plain text: {', '.join(sims)}")

        if errors:
            print("  errors:")
            for e in errors:
                print(f"    {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only check of which model wrote the Section embeddings.")
    parser.add_argument("--samples", type=int, default=5, help="sections to sample per vector size")
    parser.add_argument("--doc", default=None, help="only documents whose hash/id/name contains this")
    parser.add_argument("--list-docs", action="store_true", help="print every document with its vector sizes")
    parser.add_argument("--quick", action="store_true",
                        help="estimate vector sizes from a random sample instead of reading every vector")
    parser.add_argument("--bge", action="store_true", help="also test BAAI/bge-large-en-v1.5 locally")
    parser.add_argument("--alt-base-url", default=None, help="second endpoint to test")
    parser.add_argument("--alt-model", default=None)
    parser.add_argument("--alt-api-key", default="EMPTY")
    args = parser.parse_args()
    # Output is usually redirected to a file: flush every line so progress is visible.
    sys.stdout.reconfigure(line_buffering=True)

    be = _be_endpoint()
    endpoints = [be]
    if args.alt_base_url or args.alt_model:
        endpoints.append({
            "name": "alt", "model": args.alt_model or "", "base_url": args.alt_base_url or "",
            "api_key": args.alt_api_key, "url_from_fallback": False,
        })

    report_config(be)
    for ep in endpoints:
        report_server(ep)

    uri = os.environ.get("NEO4J_URI")
    if not uri:
        print("\nNEO4J_URI is not set — skipping steps 3 and 4.")
        return 1
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
        # V2's recipe references s.text_en, which doesn't exist in the DB — silence that warning
        notifications_min_severity="OFF",
    )
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            pool = sample_pool(session, POOL_SIZE, args.doc)
            if args.quick:
                dims = report_neo4j_quick(session, pool, args.list_docs)
            else:
                dims = report_neo4j(session, args.list_docs)
            print()
            cands = build_candidates(endpoints, args.bge)
            fingerprint(session, dims, cands, args.samples, args.doc, pool)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
