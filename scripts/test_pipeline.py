"""
SunnitAI pipeline test script.

Runs the full ingestion pipeline on a single PDF:
  parse → analisi (LLM) → consolida_analisi → flatten → Neo4J write

Usage (on the server, inside the virtualenv):
    source /opt/sunnitai-be/venv/bin/activate
    cd /opt/sunnitai-be
    python scripts/test_pipeline.py <path-to-pdf> [--template HINT] [--skip-llm]

Options:
    --template HINT   Force a parser template (e.g. banca, regolamento)
    --skip-llm        Parse only, skip analisi/LLM steps — still writes parse
                      nodes to Neo4J if configured

The script does NOT move the source PDF.
Results are saved alongside the PDF as <stem>_graph.json and <stem>_parse.json.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────────
# Works both locally (src/be/src on PYTHONPATH) and on the server
# (/opt/sunnitai-be/src/be/src in the venv environment).
_SCRIPT_DIR = Path(__file__).resolve().parent          # scripts/
_ROOT = _SCRIPT_DIR.parent                             # project root
_SRC = _ROOT / "src" / "be" / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

# Load .env from project root (server: /opt/sunnitai-be/.env)
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=False)      # local dev override

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("test_pipeline")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _save_json(data, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Saved: %s", path)


def _section(title: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}\n  {title}\n{bar}")


# ── Pipeline steps ─────────────────────────────────────────────────────────────

def step_parse(pdf_path: str, file_name: str, template_hint: str | None) -> tuple[list, dict]:
    """Returns (articoli, profile_dict)."""
    from lex_package.parse import parse
    from lex_package.parsing_utils.document_profiler import profile_document

    _section("STEP 1 — Parse")
    t0 = time.time()
    profile = profile_document(pdf_path, file_name, template_hint=template_hint)
    print(f"  Template selected : {profile.detected_type}")
    print(f"  Confidence        : {profile.confidence:.2f}")
    print(f"  Scores            : {profile.scores}")
    print(f"  has_articolo_in_body: {profile.has_articolo_in_body}")

    articoli = parse(pdf_path, file_name, template_hint=template_hint)
    elapsed = time.time() - t0

    n_commi = sum(len(a.get("commi", [])) for a in articoli)
    n_sottocommi = sum(
        len(c.get("sottocommi", []))
        for a in articoli
        for c in a.get("commi", [])
    )
    print(f"  Articoli          : {len(articoli)}")
    print(f"  Commi             : {n_commi}")
    print(f"  Sottocommi        : {n_sottocommi}")
    print(f"  Time              : {elapsed:.1f}s")

    return articoli, {
        "template": profile.detected_type,
        "confidence": profile.confidence,
        "scores": profile.scores,
        "has_articolo_in_body": profile.has_articolo_in_body,
        "template_meta": profile.template_meta,
    }


async def step_analisi(pdf_path: str, file_name: str) -> tuple[list, list]:
    """Returns (raw_analisi, consolidated)."""
    from lex_package.analisi import analisi, consolida_analisi

    _section("STEP 2 — Analisi (LLM)")
    t0 = time.time()
    print("  Calling analisi... (this may take several minutes)")
    raw = await analisi(pdf_path, file_name)
    print(f"  Raw analisi items : {len(raw) if isinstance(raw, list) else 'n/a'}")

    print("  Consolidating...")
    consolidated = await consolida_analisi(raw)
    elapsed = time.time() - t0
    print(f"  Consolidated items: {len(consolidated) if isinstance(consolidated, list) else 'n/a'}")
    print(f"  Time              : {elapsed:.1f}s")
    return raw, consolidated


def step_flatten(consolidated: list) -> list:
    from lex_package.utils.flatten import flatten_analisi_invertito

    _section("STEP 3 — Flatten")
    t0 = time.time()
    flattened = flatten_analisi_invertito(consolidated)
    elapsed = time.time() - t0
    print(f"  Flattened records : {len(flattened)}")
    print(f"  Time              : {elapsed:.1f}s")
    return flattened


def step_build_graph(flattened: list, doc_name: str, file_name: str,
                     doc_hash: str, pdf_path: str) -> dict:
    from lex_package.utils.flatten import build_neo4j_graph_payload

    _section("STEP 4 — Build Neo4J graph payload")
    payload = build_neo4j_graph_payload(
        flattened,
        document_name=doc_name,
        document_hash=doc_hash,
        file_name=file_name,
        pdf_path=pdf_path,
    )
    nodes = payload.get("nodes", [])
    rels = payload.get("relationships", [])
    print(f"  Nodes             : {len(nodes)}")
    print(f"  Relationships     : {len(rels)}")

    # Show label breakdown
    from collections import Counter
    label_counts: Counter = Counter()
    for n in nodes:
        for lbl in n.get("labels", ["Unknown"]):
            label_counts[lbl] += 1
    print("  Node labels       :")
    for lbl, cnt in label_counts.most_common():
        print(f"    {lbl}: {cnt}")

    return payload


def step_neo4j(payload: dict) -> tuple[int, int]:
    from lex_package.utils.graph_writer import is_configured, write_graph_payload

    _section("STEP 5 — Neo4J write")
    if not is_configured():
        print("  NEO4J_URI is not set — skipping DB write.")
        print("  Set NEO4J_URI in .env to enable this step.")
        return 0, 0

    print(f"  Connecting to: {os.environ.get('NEO4J_URI')}")
    t0 = time.time()
    nodes_written, rels_written = write_graph_payload(payload)
    elapsed = time.time() - t0
    print(f"  Nodes written     : {nodes_written}")
    print(f"  Relationships     : {rels_written}")
    print(f"  Time              : {elapsed:.1f}s")
    return nodes_written, rels_written


# ── Build a minimal graph payload from parse-only results (no LLM) ─────────────

def _parse_only_payload(articoli: list, doc_name: str, file_name: str,
                        doc_hash: str, pdf_path: str) -> dict:
    """
    Build a lightweight graph payload directly from articoli (no LLM analysis).
    Creates Document → Articolo → Comma nodes with basic properties.
    """
    nodes = []
    rels = []
    doc_id = f"doc::{doc_hash[:16]}"

    nodes.append({
        "id": doc_id,
        "labels": ["Document"],
        "properties": {
            "name": doc_name,
            "file_name": file_name,
            "pdf_path": pdf_path,
            "hash": doc_hash,
            "source": "parse_only",
        },
    })

    for i, art in enumerate(articoli):
        art_id = f"{doc_id}::art::{i}"
        art_props = {
            "numero": art.get("numero", ""),
            "titolo": art.get("titolo", ""),
            "identificativo": art.get("identificativo", ""),
            "source": "parse_only",
        }
        nodes.append({"id": art_id, "labels": ["Articolo"], "properties": art_props})
        rels.append({"source": doc_id, "target": art_id, "type": "HAS_ARTICOLO", "properties": {}})

        for j, comma in enumerate(art.get("commi", [])):
            comma_id = f"{art_id}::comma::{j}"
            nodes.append({
                "id": comma_id,
                "labels": ["Comma"],
                "properties": {
                    "numero": comma.get("numero", ""),
                    "contenuto": (comma.get("contenuto", "") or "")[:500],
                    "source": "parse_only",
                },
            })
            rels.append({"source": art_id, "target": comma_id, "type": "HAS_COMMA", "properties": {}})

    return {"nodes": nodes, "relationships": rels}


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"ERROR: file not found: {pdf_path}")
        sys.exit(1)

    file_name = pdf_path.name
    doc_name = pdf_path.stem
    pdf_path_str = str(pdf_path)
    out_dir = pdf_path.parent

    print(f"\n{'═' * 60}")
    print(f"  SunnitAI pipeline test")
    print(f"  File    : {file_name}")
    print(f"  Skip LLM: {args.skip_llm}")
    if args.template:
        print(f"  Template hint: {args.template}")
    print(f"{'═' * 60}")

    doc_hash = _sha256(pdf_path)

    # ── Parse ──────────────────────────────────────────────────────────────────
    articoli, profile_info = step_parse(pdf_path_str, file_name, args.template)
    _save_json({"profile": profile_info, "articoli": articoli},
               out_dir / f"{doc_name}_parse.json")

    if args.skip_llm:
        # Parse-only path: build lightweight graph directly from articoli
        _section("LLM skipped — building parse-only graph payload")
        payload = _parse_only_payload(articoli, doc_name, file_name, doc_hash, pdf_path_str)
        print(f"  Nodes       : {len(payload['nodes'])}")
        print(f"  Relationships: {len(payload['relationships'])}")
    else:
        # ── Full LLM pipeline ──────────────────────────────────────────────────
        try:
            raw, consolidated = await step_analisi(pdf_path_str, file_name)
            _save_json({"raw": raw, "consolidated": consolidated},
                       out_dir / f"{doc_name}_analisi.json")
        except Exception as exc:
            logger.error("Analisi failed: %s", exc)
            print("\n  LLM call failed. Re-run with --skip-llm to test parse + Neo4J only.")
            sys.exit(1)

        flattened = step_flatten(consolidated)
        _save_json(flattened, out_dir / f"{doc_name}_flat.json")

        payload = step_build_graph(flattened, doc_name, file_name, doc_hash, pdf_path_str)

    _save_json(payload, out_dir / f"{doc_name}_graph.json")

    # ── Neo4J ──────────────────────────────────────────────────────────────────
    nodes_written, rels_written = step_neo4j(payload)

    # ── Summary ────────────────────────────────────────────────────────────────
    _section("SUMMARY")
    print(f"  File              : {file_name}")
    print(f"  Template          : {profile_info['template']} (confidence {profile_info['confidence']:.2f})")
    print(f"  Articoli parsed   : {len(articoli)}")
    print(f"  Neo4J nodes       : {nodes_written}")
    print(f"  Neo4J rels        : {rels_written}")
    print(f"  Output files      : {out_dir / doc_name}_*.json")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SunnitAI pipeline test")
    parser.add_argument("pdf", help="Path to the PDF file to process")
    parser.add_argument("--template", default=None,
                        help="Force template (e.g. banca, regolamento)")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip analisi LLM step, use parse-only graph payload")
    args = parser.parse_args()
    asyncio.run(main(args))
