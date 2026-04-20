from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _safe_stem(p: Path) -> str:
    # Keep original stem; CLI flatten uses prefix matching anyway.
    return p.stem


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="Base folder containing PDFs (recursive).")
    ap.add_argument(
        "--dest-root",
        required=True,
        help="Destination root, e.g. ...\\Documenti Analizzati\\20260331",
    )
    args = ap.parse_args()

    base = Path(args.base)
    dest_root = Path(args.dest_root)
    if not base.exists():
        raise SystemExit(f"Base folder not found: {base}")

    # Make sure `lex_package` is importable when launching from repo root
    # (lex_package lives under src/be/src).
    repo_root = Path(__file__).resolve().parents[1]
    lex_src = repo_root / "src" / "be" / "src"
    core_src = repo_root / "src"
    # Make both `lex_package` and `core` importable.
    for p in (lex_src, core_src):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    # Lazy imports (need repo python path configured by caller working dir)
    from lex_package.analisi import analisi, consolida_analisi
    from lex_package.utils.flatten import (
        build_neo4j_graph_payload,
        stable_hash,
        flatten_analisi_invertito,
    )
    from lex_package.utils.to_xlsx import write_records_to_xlsx

    out_analisi_dir = repo_root / "out_analisi"
    out_flat_dir = repo_root / "out_flat" / "out_analisi"
    out_analisi_dir.mkdir(parents=True, exist_ok=True)
    out_flat_dir.mkdir(parents=True, exist_ok=True)
    dest_root.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(base.rglob("*.pdf"))
    print(f"Found {len(pdfs)} pdf(s) under {base}")

    import asyncio

    async def _run_one(pdf: Path):
        name = pdf.name
        stem = _safe_stem(pdf)
        rel = pdf.relative_to(base)
        rel_dir = rel.parent

        print(f"\n=== ANALISI: {rel} ===")
        res = await analisi(str(pdf), name)
        res = await consolida_analisi(res)

        out_json = out_analisi_dir / f"{stem}.json"
        out_json.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

        # Flatten + XLSX (without importing full lex_package.cli)
        flattened = flatten_analisi_invertito(res)
        (out_flat_dir / f"{stem}.json").write_text(
            json.dumps(flattened, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # XLSX: esclude liste raw; colonna "Vettore" = serializzazione come Embedding
        xlsx_records = []
        for rec in flattened:
            row = {k: v for k, v in rec.items() if k not in ("Embedding Raw", "Vettore", "Embedding")}
            row["Vettore"] = rec.get("Embedding") or ""
            xlsx_records.append(row)
        write_records_to_xlsx(xlsx_records, out_flat_dir / f"{stem}.xlsx")

        # Graph payload from flattened (use doc hash derived from document name)
        doc_hash = stable_hash(name)
        graph = build_neo4j_graph_payload(
            flattened,
            document_name=name,
            document_hash=doc_hash,
            file_name=name,
            pdf_path=str(pdf),
        )
        graph_path = out_flat_dir / f"{stem}_graph.json"
        graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

        # Copy outputs to dest preserving relative directory
        dest_dir = dest_root / rel_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 1) raw analysis json
        _copy_if_exists(out_json, dest_dir / out_json.name)

        # 2) flattened json + xlsx
        _copy_if_exists(out_flat_dir / f"{stem}.json", dest_dir / f"{stem}_flattened.json")
        _copy_if_exists(out_flat_dir / f"{stem}.xlsx", dest_dir / f"{stem}_flattened.xlsx")

        # 3) graph json
        _copy_if_exists(graph_path, dest_dir / graph_path.name)

        print(f"Copied outputs to {dest_dir}")

    async def _run_all():
        for pdf in pdfs:
            await _run_one(pdf)

    asyncio.run(_run_all())
    print("\nAll analyses completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

