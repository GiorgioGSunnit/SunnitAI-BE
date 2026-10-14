#!/usr/bin/env python3
"""
Embedding retrieval evaluation: how often does vector search find the right section,
today versus after the fix (plain text instead of OpenAI token IDs)?

READ-ONLY: Neo4J is only read (MATCH and the vector index query). The chat model writes test
questions and the embedding server embeds texts. Nothing is stored anywhere, except the
optional --out JSON file.

What it does:
  1. Samples CORPUS random Sections that have a stored vector (default 2000). The first QUESTIONS
     of them with enough text (default 200) become the targets.
  2. For each target, the chat model writes two questions that the section answers:
       everyday   how a normal person would ask, in their own words
       expert     how a lawyer would ask, with the terms of the text
  3. Ranks the corpus for every question with each configuration and records where the
     target lands:
       current        stored vectors + question sent as token IDs (what production does today)
       new/A          plain text, document = abstract + plain_text
       new/A+instr    same, question prefixed with the Qwen query instruction
       new/B          plain text, document = title + abstract + plain_text
                      (abstract dropped when it only repeats the start of plain_text)
       new/B+instr    same, with the instruction
  4. Queries the real production index (section_embeddings, every section) with today's
     method, to show today's real recall at k=30.
  5. Prints recall@1/5/10/30, MRR, score distributions and a suggested min_score per
     configuration, and checks whether the server can truncate texts over 8192 tokens.

This measures vector search only. Production also runs keyword search (BM25) alongside it.

Usage (on the server, inside the BE virtualenv; run off-peak, it uses the shared GPU):
    cd /opt/sunnitai-be
    venv/bin/python scripts/eval_embeddings.py [--questions 200] [--corpus 2000] [--workers 4] [--out /tmp/eval.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

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


QWEN_QUERY_INSTRUCTION = (
    "Instruct: Given a legal question in Italian, retrieve the passages of laws, "
    "rulings and commentary that answer it\nQuery: "
)
MAX_DOC_CHARS = 20000      # keeps every text under the server's 8192-token limit for this test
MIN_TARGET_CHARS = 200     # sections shorter than this make poor question material
TIMEOUT = 90
K_LIST = (1, 5, 10, 30)
CURRENT_MIN_SCORE = 0.25   # V2's min_score for section_embeddings today

PROMPTS = {
    "everyday": (
        "Scrivi UNA domanda che una persona comune, senza conoscenze giuridiche, farebbe a un "
        "assistente legale e a cui questo estratto risponde. Usa parole di tutti i giorni: non "
        "copiare frasi o termini tecnici del testo, non citare numeri di articolo, leggi o sentenze. "
        "Rispondi solo con la domanda."
    ),
    "expert": (
        "Scrivi UNA domanda precisa, come la farebbe un avvocato, a cui questo estratto risponde. "
        "Puoi usare i termini tecnici del testo. Rispondi solo con la domanda."
    ),
}


def log(msg: str) -> None:
    print(msg, flush=True)


def _header(title: str) -> None:
    log(f"\n{'═' * 78}\n{title}\n{'═' * 78}")


# ── Clients ────────────────────────────────────────────────────────────────────

def _embedding_endpoint() -> tuple[str, str, str]:
    """Same lookup order as lex_package.llm.factory.build_embedding_model."""
    url = os.getenv("LLM_EMBEDDING_BASE_URL") or os.getenv("LLM_BASE_URL") or ""
    key = (os.getenv("LLM_EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY")
           or os.getenv("OPENAI_API_KEY") or "EMPTY")
    return url, os.getenv("LLM_EMBEDDING_MODEL") or "", key


def _openai(base_url: str, api_key: str):
    from openai import OpenAI
    return OpenAI(base_url=base_url or None, api_key=api_key, timeout=TIMEOUT, max_retries=1)


def _token_id_client():
    """Today's production method: LangChain OpenAIEmbeddings with default settings
    (cl100k token IDs). Same arguments as BE's factory, plus a timeout."""
    from langchain_openai import OpenAIEmbeddings
    url, model, key = _embedding_endpoint()
    return OpenAIEmbeddings(model=model, api_key=key, base_url=url or None,
                            timeout=TIMEOUT, max_retries=1)


# ── Texts ──────────────────────────────────────────────────────────────────────

def recipe_a(r: dict) -> str:
    return f"{r['abstract']}\n{r['plain_text']}".strip()[:MAX_DOC_CHARS]


def recipe_b(r: dict) -> str:
    abstract = r["abstract"].strip()
    plain = r["plain_text"].strip()
    # BE sets abstract = left(plain_text, 200) when it has none: don't embed that twice.
    if abstract and plain.startswith(abstract[:150]):
        abstract = ""
    return "\n".join(p for p in (r["title"].strip(), abstract, plain) if p)[:MAX_DOC_CHARS]


def _clean_question(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().strip('"“”«»').strip()
        if line:
            return line
    return ""


# ── Embedding helpers ──────────────────────────────────────────────────────────

def embed_plain(client, model: str, texts: list[str], workers: int, batch: int = 8) -> list:
    """Plain-text embeddings; a failing batch is retried item by item. Failed items -> None."""
    def one_batch(chunk: list[str]) -> list:
        try:
            resp = client.embeddings.create(model=model, input=chunk, encoding_format="float")
            return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        except Exception:
            out = []
            for t in chunk:
                try:
                    r = client.embeddings.create(model=model, input=[t], encoding_format="float")
                    out.append(r.data[0].embedding)
                except Exception:
                    out.append(None)
            return out

    chunks = [texts[i:i + batch] for i in range(0, len(texts), batch)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one_batch, chunks))
    return [v for chunk in results for v in chunk]


def embed_token_ids(texts: list[str], workers: int) -> list:
    client = _token_id_client()

    def one(t: str):
        try:
            return client.embed_documents([t])[0]
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, texts))


def normalize(rows: list) -> np.ndarray:
    m = np.asarray(rows, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


# ── Metrics ────────────────────────────────────────────────────────────────────

def evaluate(q_mat: np.ndarray, doc_mat: np.ndarray, target_idx: np.ndarray) -> dict:
    """Rank of the target section for every question, its score, and the 30 best wrong scores."""
    sims = q_mat @ doc_mat.T
    rows = np.arange(len(target_idx))
    target_scores = sims[rows, target_idx]
    ranks = (sims > target_scores[:, None]).sum(axis=1) + 1
    wrong = sims.copy()
    wrong[rows, target_idx] = -np.inf
    top_wrong = np.sort(wrong, axis=1)[:, -30:]
    return {"ranks": ranks, "target_scores": target_scores, "top_wrong": top_wrong}


def recall_row(ranks: np.ndarray) -> str:
    cells = "".join(f"{np.mean(ranks <= k):>7.2f}" for k in K_LIST)
    return f"{cells}{np.mean(1.0 / ranks):>7.2f}"


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only evaluation of embedding retrieval, today vs fixed.")
    parser.add_argument("--questions", type=int, default=200, help="target sections (2 questions each)")
    parser.add_argument("--corpus", type=int, default=2000, help="sections to rank against (targets included)")
    parser.add_argument("--workers", type=int, default=4, help="parallel requests to the GPU servers")
    parser.add_argument("--skip-index", action="store_true", help="skip the query against the real production index")
    parser.add_argument("--out", default=None, help="write per-question results as JSON")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    t_start = time.time()

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "")),
        notifications_min_severity="OFF",
    )
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    emb_url, emb_model, emb_key = _embedding_endpoint()
    emb_client = _openai(emb_url, emb_key)
    chat_client = _openai(os.getenv("LLM_BASE_URL") or "",
                          os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "EMPTY")
    chat_model = os.getenv("LLM_MODEL") or ""

    # 1. Corpus
    _header("1. Sampling sections")
    with driver.session(database=database) as session:
        rows = session.run("""
            MATCH (d:Document)-[:CONTAINS]->(s:Section) WHERE s.embedding IS NOT NULL
            WITH d, s ORDER BY rand() LIMIT $n
            RETURN elementId(s) AS eid, coalesce(d.name, d.id) AS doc,
                   coalesce(s.title, '') AS title, coalesce(s.abstract, '') AS abstract,
                   coalesce(s.plain_text, '') AS plain_text, s.embedding AS stored""",
            n=args.corpus).data()
    corpus = [r for r in rows if r["stored"] and len(r["stored"]) == 4096 and r["plain_text"].strip()]
    targets = [i for i, r in enumerate(corpus) if len(r["plain_text"]) >= MIN_TARGET_CHARS][:args.questions]
    log(f"  Neo4J: {os.environ['NEO4J_URI']}")
    log(f"  corpus: {len(corpus)} sections, targets: {len(targets)}")

    # 2. Questions
    _header("2. Writing test questions with the chat model")
    log(f"  chat model: {chat_model} at {os.getenv('LLM_BASE_URL')}")
    jobs = [(i, style) for i in targets for style in PROMPTS]

    def ask(job):
        i, style = job
        text = corpus[i]["plain_text"][:3000]
        try:
            resp = chat_client.chat.completions.create(
                model=chat_model, temperature=0.7, max_tokens=120,
                messages=[{"role": "user", "content": f"Estratto:\n<<<\n{text}\n>>>\n\n{PROMPTS[style]}"}],
            )
            return _clean_question(resp.choices[0].message.content)
        except Exception as exc:
            log(f"  question failed: {str(exc)[:120]}")
            return ""

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        texts = list(ex.map(ask, jobs))
    questions = [(i, style, q) for (i, style), q in zip(jobs, texts) if q]
    log(f"  questions written: {len(questions)} of {len(jobs)}")

    # 3. Embeddings
    _header("3. Embedding (in memory only)")
    q_texts = [q for _, _, q in questions]
    t = time.time()
    log("  documents, recipe A ...")
    docs_a = embed_plain(emb_client, emb_model, [recipe_a(r) for r in corpus], args.workers)
    log("  documents, recipe B ...")
    docs_b = embed_plain(emb_client, emb_model, [recipe_b(r) for r in corpus], args.workers)
    log("  questions: token IDs (today), plain, plain + instruction ...")
    q_old = embed_token_ids(q_texts, args.workers)
    q_plain = embed_plain(emb_client, emb_model, q_texts, args.workers)
    q_instr = embed_plain(emb_client, emb_model, [QWEN_QUERY_INSTRUCTION + q for q in q_texts], args.workers)
    log(f"  done in {time.time() - t:.0f}s")

    # Keep only documents and questions that every configuration could embed.
    doc_ok = [k for k in range(len(corpus)) if docs_a[k] is not None and docs_b[k] is not None]
    doc_pos = {k: n for n, k in enumerate(doc_ok)}
    q_ok = [n for n, (i, _, _) in enumerate(questions)
            if i in doc_pos and q_old[n] is not None and q_plain[n] is not None and q_instr[n] is not None]
    dropped_docs = len(corpus) - len(doc_ok)
    log(f"  usable: {len(doc_ok)} documents ({dropped_docs} failed), "
        f"{len(q_ok)} questions ({len(questions) - len(q_ok)} dropped)")

    target_idx = np.array([doc_pos[questions[n][0]] for n in q_ok])
    styles = np.array([questions[n][1] for n in q_ok])
    stored = normalize([corpus[k]["stored"] for k in doc_ok])
    mat_a = normalize([docs_a[k] for k in doc_ok])
    mat_b = normalize([docs_b[k] for k in doc_ok])
    mq_old = normalize([q_old[n] for n in q_ok])
    mq_plain = normalize([q_plain[n] for n in q_ok])
    mq_instr = normalize([q_instr[n] for n in q_ok])

    configs = {
        "current": evaluate(mq_old, stored, target_idx),
        "new/A": evaluate(mq_plain, mat_a, target_idx),
        "new/A+instr": evaluate(mq_instr, mat_a, target_idx),
        "new/B": evaluate(mq_plain, mat_b, target_idx),
        "new/B+instr": evaluate(mq_instr, mat_b, target_idx),
    }

    # 4. Recall on the sample
    _header(f"4. Recall on the sample (vector search only, {len(doc_ok)} sections)")
    log(f"  {'config':<14}{'questions':<10}" + "".join(f"{'@' + str(k):>7}" for k in K_LIST) + f"{'MRR':>7}")
    for name, res in configs.items():
        for style in ("everyday", "expert", "all"):
            mask = np.ones(len(styles), bool) if style == "all" else styles == style
            log(f"  {name:<14}{style:<10}{recall_row(res['ranks'][mask])}")
        log("")

    # 5. Today on the real production index
    if not args.skip_index:
        _header("5. Today in production: real index section_embeddings, k=30, today's method")
        found = []
        with driver.session(database=database) as session:
            for pos, n in enumerate(q_ok):
                hits = session.run(
                    "CALL db.index.vector.queryNodes('section_embeddings', 30, $v) "
                    "YIELD node, score RETURN elementId(node) AS eid",
                    v=[float(x) for x in q_old[n]],
                ).data()
                eids = [h["eid"] for h in hits]
                target_eid = corpus[questions[n][0]]["eid"]
                found.append(eids.index(target_eid) + 1 if target_eid in eids else 10**6)
        found = np.array(found)
        log(f"  {'questions':<10}" + "".join(f"{'@' + str(k):>7}" for k in K_LIST))
        for style in ("everyday", "expert", "all"):
            mask = np.ones(len(styles), bool) if style == "all" else styles == style
            log(f"  {style:<10}" + "".join(f"{np.mean(found[mask] <= k):>7.2f}" for k in K_LIST))

    # 6. Scores and thresholds
    _header("6. Scores of the right section, and a suggested min_score")
    log("  suggested min_score = score that keeps 90% of the right sections (10th percentile)")
    log(f"  {'config':<14}{'right: median':>14}{'p10':>7}{'wrong top-30: median':>22}{'p90':>7}"
        f"{'suggested':>11}{'wrong above it':>16}")
    for name, res in configs.items():
        ts, tw = res["target_scores"], res["top_wrong"]
        suggested = float(np.percentile(ts, 10))
        log(f"  {name:<14}{np.median(ts):>14.3f}{np.percentile(ts, 10):>7.3f}"
            f"{np.median(tw):>22.3f}{np.percentile(tw, 90):>7.3f}"
            f"{suggested:>11.3f}{np.mean(tw >= suggested):>16.0%}")
    cur = configs["current"]
    log(f"\n  today's min_score {CURRENT_MIN_SCORE}: keeps {np.mean(cur['target_scores'] >= CURRENT_MIN_SCORE):.0%} "
        f"of right sections, lets through {np.mean(cur['top_wrong'] >= CURRENT_MIN_SCORE):.0%} of wrong top-30 results")

    # 7. Long texts
    _header("7. Can the server truncate texts over 8192 tokens? (one request)")
    try:
        emb_client.embeddings.create(model=emb_model, input=["parola " * 20000], encoding_format="float",
                                     extra_body={"truncate_prompt_tokens": 8000})
        log("  yes: a ~20,000-token text was accepted with truncate_prompt_tokens=8000")
    except Exception as exc:
        log(f"  no: {str(exc)[:200]}")

    # 8. Examples
    _header("8. Examples: everyday questions, where the right section ranked")
    best = max((k for k in configs if k != "current"),
               key=lambda k: np.mean(1.0 / configs[k]["ranks"]))
    shown = 0
    for pos, n in enumerate(q_ok):
        if styles[pos] != "everyday" or shown >= 8:
            continue
        i, _, q = questions[n]
        log(f"  Q: {q}")
        log(f"     section: {corpus[i]['doc']}")
        log(f"     rank today: {configs['current']['ranks'][pos]}   rank with {best}: {configs[best]['ranks'][pos]}")
        shown += 1

    if args.out:
        out = [{
            "question": questions[n][2],
            "style": questions[n][1],
            "document": corpus[questions[n][0]]["doc"],
            "section_eid": corpus[questions[n][0]]["eid"],
            "ranks": {name: int(res["ranks"][pos]) for name, res in configs.items()},
            "scores": {name: round(float(res["target_scores"][pos]), 4) for name, res in configs.items()},
        } for pos, n in enumerate(q_ok)]
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"\n  per-question results written to {args.out}")

    driver.close()
    log(f"\nDone in {(time.time() - t_start) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
