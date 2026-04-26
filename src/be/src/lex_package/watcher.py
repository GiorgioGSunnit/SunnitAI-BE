"""
SunnitAI filesystem watcher — monitors WATCH_DIR for new PDF files and runs the
full ingestion pipeline: parse → analisi → flatten → Neo4J write.

Env vars:
    WATCH_DIR           /opt/sunnitai-be/inbox   Directory to watch for PDFs
    NEO4J_URI           (optional) bolt://host:7687  — skips DB write if unset
    NEO4J_USER          neo4j
    NEO4J_PASSWORD
    NEO4J_DATABASE      neo4j
    LLM_API_KEY / LLM_BASE_URL / LLM_MODEL   — for metadata extraction (optional)

Usage:
    python -m lex_package.watcher          # as module (recommended, sets PYTHONPATH)
    python watcher.py                      # as script from lex_package directory

On success, the PDF is moved to <WATCH_DIR>/done/.
On failure, the PDF is moved to <WATCH_DIR>/failed/.
Existing PDFs in WATCH_DIR are processed once at startup before the watch loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sunnitai.watcher")

# ── Path setup ─────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent        # .../lex_package
_SRC_DIR = _SCRIPT_DIR.parent                        # .../src
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ── Pipeline imports (deferred so startup errors are visible) ──────────────────
from lex_package.analisi import analisi, consolida_analisi
from lex_package.utils.flatten import flatten_analisi_invertito, build_neo4j_graph_payload
from lex_package.utils.graph_writer import is_configured, write_graph_payload


# ── Config ─────────────────────────────────────────────────────────────────────
WATCH_DIR = Path(os.environ.get("WATCH_DIR", "/opt/sunnitai-be/inbox"))
POLL_INTERVAL = int(os.environ.get("WATCHER_POLL_SECONDS", "10"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def process_pdf(pdf_path: Path, done_dir: Path, failed_dir: Path) -> bool:
    """
    Run the full pipeline on a single PDF file.

    Returns True on success, False on failure.
    Moves the file to done_dir or failed_dir accordingly.
    """
    doc_name = pdf_path.stem          # filename without extension
    file_name = pdf_path.name         # e.g. "Regolamento_XYZ.pdf"
    pdf_path_str = str(pdf_path)

    logger.info("Processing: %s", file_name)

    try:
        # ── 1. Parse + analyse ─────────────────────────────────────────────────
        logger.info("  [1/4] Running analisi...")
        raw = await analisi(pdf_path_str, file_name)
        consolidated = await consolida_analisi(raw)

        # ── 2. Flatten ─────────────────────────────────────────────────────────
        logger.info("  [2/4] Flattening analysis...")
        flattened = flatten_analisi_invertito(consolidated)

        # ── 3. Build Neo4J graph payload ───────────────────────────────────────
        logger.info("  [3/4] Building graph payload...")
        doc_hash = _sha256_file(pdf_path)
        payload = build_neo4j_graph_payload(
            flattened,
            document_name=doc_name,
            document_hash=doc_hash,
            file_name=file_name,
            pdf_path=pdf_path_str,
        )

        # Save payload as JSON alongside the processed file (audit trail)
        graph_json_path = done_dir / f"{doc_name}_graph.json"
        done_dir.mkdir(parents=True, exist_ok=True)
        with open(graph_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("  Graph payload saved to %s", graph_json_path)

        # ── 4. Write to Neo4J (optional) ───────────────────────────────────────
        if is_configured():
            logger.info("  [4/4] Writing to Neo4J...")
            nodes_written, rels_written = write_graph_payload(payload)
            logger.info(
                "  Neo4J: wrote %d nodes and %d relationships", nodes_written, rels_written
            )
        else:
            logger.info(
                "  [4/4] NEO4J_URI not set — skipping DB write "
                "(graph payload saved to %s)",
                graph_json_path,
            )

        # ── Move to done ───────────────────────────────────────────────────────
        dest = done_dir / file_name
        shutil.move(str(pdf_path), str(dest))
        logger.info("  Done: moved to %s", dest)
        return True

    except Exception as exc:
        logger.exception("  Failed processing %s: %s", file_name, exc)
        failed_dir.mkdir(parents=True, exist_ok=True)
        dest = failed_dir / file_name
        try:
            shutil.move(str(pdf_path), str(dest))
            logger.info("  Moved failed file to %s", dest)
        except Exception as move_exc:
            logger.error("  Could not move failed file: %s", move_exc)
        return False


def _pdf_files_in(directory: Path) -> list[Path]:
    """Return all .pdf files directly inside directory (non-recursive)."""
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


async def run_watcher():
    """
    Main watcher loop.

    1. Creates WATCH_DIR, done/, and failed/ directories if they don't exist.
    2. Processes any PDFs already present at startup.
    3. Polls for new PDFs every POLL_INTERVAL seconds.
    """
    done_dir = WATCH_DIR / "done"
    failed_dir = WATCH_DIR / "failed"

    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    neo4j_status = "enabled" if is_configured() else "disabled (NEO4J_URI not set)"
    logger.info("Watcher starting — WATCH_DIR=%s, Neo4J=%s", WATCH_DIR, neo4j_status)
    logger.info("Poll interval: %ds", POLL_INTERVAL)

    # ── Process existing PDFs at startup ──────────────────────────────────────
    existing = _pdf_files_in(WATCH_DIR)
    if existing:
        logger.info("Found %d existing PDF(s) — processing before watch loop...", len(existing))
        for pdf in existing:
            await process_pdf(pdf, done_dir, failed_dir)
    else:
        logger.info("No existing PDFs — ready and watching for new files...")

    # ── Watch loop ────────────────────────────────────────────────────────────
    seen: set[str] = set()  # filenames already queued in this session

    while True:
        try:
            current = _pdf_files_in(WATCH_DIR)
            for pdf in current:
                if pdf.name not in seen:
                    seen.add(pdf.name)
                    logger.info("New PDF detected: %s", pdf.name)
                    await process_pdf(pdf, done_dir, failed_dir)
                    # After processing, file has been moved — remove from seen
                    # so a file with the same name can be reprocessed later
                    seen.discard(pdf.name)
        except Exception as exc:
            logger.exception("Unexpected error in watcher loop: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


def main():
    try:
        asyncio.run(run_watcher())
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user.")


if __name__ == "__main__":
    main()
