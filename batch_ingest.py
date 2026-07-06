#!/usr/bin/env python3
"""
Batch ingestion daemon — watches /opt/sunnitai-be/document_upload/ for PDFs,
ingests each one, deletes on success, moves to /opt/sunnitai-be/document_failed/
on failure. Runs continuously or as a one-shot pass (--once flag).

Usage:
    python3 batch_ingest.py          # continuous watch mode
    python3 batch_ingest.py --once   # process current files and exit

Document name is derived from filename stem, with common normalizations.
Override by placing a .name file alongside the PDF:
    "Codice Penale - 2026.pdf" + "Codice Penale - 2026.name" containing "Codice Penale 2026"
"""
import asyncio
import hashlib
import logging
import os
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, '/opt/sunnitai-be/src/be/src')
sys.path.insert(0, '/opt/sunnitai-be/src/be/azure-durable-function')

UPLOAD_DIR  = Path("/opt/sunnitai-be/document_upload")
FAILED_DIR  = Path("/opt/sunnitai-be/document_failed")
LOG_FILE    = Path("/var/log/sunnitai-ingest.log")
POLL_INTERVAL = 30  # seconds between directory scans

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
logger = logging.getLogger("batch_ingest")


def doc_name_from_pdf(pdf_path: Path) -> str:
    """Derive document name from .name sidecar file or filename stem."""
    name_file = pdf_path.with_suffix(".name")
    if name_file.exists():
        return name_file.read_text().strip()
    stem = pdf_path.stem
    import re
    # Only strip trailing year when it's the ONLY suffix after the document name
    # e.g. "Codice Penale - 2026" → "Codice Penale"
    # but NOT "Codice Appalti - 2026-DEF pdf" — keep as-is since "-DEF pdf" is part of the name
    stem = re.sub(r'\s*-\s*20\d\d\s*$', '', stem, flags=re.IGNORECASE)
    stem = re.sub(r'\s*Edizione\s*20\d\d$', '', stem, flags=re.IGNORECASE)
    stem = stem.strip()
    return stem


def ingest_one(pdf_path: Path) -> bool:
    """Full pipeline for a single PDF. Returns True on success."""
    doc_name  = doc_name_from_pdf(pdf_path)
    file_name = pdf_path.name
    logger.info("START ▶ %s → '%s'", file_name, doc_name)
    t_start = time.perf_counter()

    try:
        file_content = pdf_path.read_bytes()
        doc_hash = hashlib.sha256(file_content).hexdigest()

        # 1. Parse + LLM
        logger.info("  [1/4] parse + LLM analysis")
        from lex_package.analisi import analisi, consolida_analisi
        t = time.perf_counter()
        raw          = asyncio.run(analisi(str(pdf_path), file_name))
        consolidated = asyncio.run(consolida_analisi(raw))
        logger.info("  [1/4] done %.1fs", time.perf_counter() - t)

        # 2. Flatten
        logger.info("  [2/4] flatten")
        from lex_package.utils.flatten import flatten_analisi_invertito, build_neo4j_graph_payload
        t = time.perf_counter()
        flattened = flatten_analisi_invertito(consolidated)
        logger.info("  [2/4] done %.1fs", time.perf_counter() - t)

        # 3. Build payload
        logger.info("  [3/4] build graph payload")
        t = time.perf_counter()
        payload = build_neo4j_graph_payload(
            flattened,
            document_name=doc_name,
            document_hash=doc_hash,
            file_name=file_name,
            pdf_path=str(pdf_path),
        )
        logger.info("  [3/4] done %.1fs", time.perf_counter() - t)

        # 3b. Deduplication
        from lex_package.utils.graph_writer import (
            is_configured, write_graph_payload,
            find_document_by_name, delete_document_by_id,
        )
        new_doc_id = f"LEGAL_DOC::{doc_hash}"
        dupes = [d for d in find_document_by_name(doc_name) if d["id"] != new_doc_id]
        if dupes:
            logger.info("  dedup: removing %d existing copy/copies", len(dupes))
            for d in dupes:
                delete_document_by_id(d["id"])

        # 4. Write with retry
        logger.info("  [4/4] write to Neo4j")
        t = time.perf_counter()
        max_attempts, backoff = 4, 10
        for attempt in range(1, max_attempts + 1):
            try:
                nodes, rels = write_graph_payload(payload)
                logger.info(
                    "  [4/4] done %.1fs — %d nodes, %d rels",
                    time.perf_counter() - t, nodes, rels,
                )
                break
            except Exception as exc:
                transient = any(k in str(exc) for k in (
                    "DatabaseUnavailable", "TransientError",
                    "transaction has been terminated", "Retry your operation",
                ))
                if transient and attempt < max_attempts:
                    logger.warning("  Neo4j transient error, retry %d/%d in %ds", attempt, max_attempts, backoff)
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    raise

        elapsed = time.perf_counter() - t_start
        logger.info("DONE ✓ '%s' in %.1fs", doc_name, elapsed)
        return True

    except Exception as exc:
        logger.error("FAILED ✗ '%s' — %s", doc_name, exc, exc_info=True)
        return False


_LOCK_TIMEOUT_SECONDS = 3600  # 1 hour — any lock older than this is stale

def _clear_stale_locks() -> None:
    """Remove lock files from crashed/killed previous runs."""
    import time
    for lock in UPLOAD_DIR.glob("*.lock"):
        age = time.time() - lock.stat().st_mtime
        if age > _LOCK_TIMEOUT_SECONDS:
            logger.warning("Removing stale lock (%.0fs old): %s", age, lock.name)
            lock.unlink(missing_ok=True)


def process_directory() -> int:
    """Process all PDFs in UPLOAD_DIR. Returns count of successes."""
    _clear_stale_locks()
    pdfs = sorted(UPLOAD_DIR.glob("*.pdf"))
    if not pdfs:
        return 0

    logger.info("Found %d PDF(s) to ingest", len(pdfs))
    successes = 0

    for pdf_path in pdfs:
        lock = pdf_path.with_suffix(".lock")
        if lock.exists():
            age = time.time() - lock.stat().st_mtime
            logger.info("SKIP (locked, %.0fs old): %s", age, pdf_path.name)
            continue

        # Claim the file
        lock.touch()
        try:
            success = ingest_one(pdf_path)
            sidecar = pdf_path.with_suffix(".name")

            if success:
                pdf_path.unlink()
                if sidecar.exists():
                    sidecar.unlink()
                successes += 1
            else:
                FAILED_DIR.mkdir(parents=True, exist_ok=True)
                dest = FAILED_DIR / pdf_path.name
                pdf_path.rename(dest)
                if sidecar.exists():
                    sidecar.rename(FAILED_DIR / sidecar.name)
                logger.warning("Moved to failed: %s", dest)
        finally:
            if lock.exists():
                lock.unlink()

        time.sleep(5)

    return successes


def main():
    parser = argparse.ArgumentParser(description="SunnitAI batch ingestion daemon")
    parser.add_argument("--once", action="store_true", help="Process current files and exit")
    args = parser.parse_args()

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Batch ingest daemon starting — watching %s", UPLOAD_DIR)

    if args.once:
        process_directory()
        return

    while True:
        try:
            process_directory()
        except Exception as exc:
            logger.error("Unexpected error in main loop: %s", exc, exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
