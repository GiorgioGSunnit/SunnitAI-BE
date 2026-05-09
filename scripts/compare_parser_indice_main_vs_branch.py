from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
REPORTS_DIR = REPO_ROOT / "reports" / "parser_comparison"
ASSETS_DIR = REPORTS_DIR / "assets"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def generate_fixture_pdf(pdf_path: Path) -> None:
    ensure_dir(pdf_path.parent)
    import fitz

    doc = fitz.open()

    page1 = doc.new_page()
    text1 = textwrap.dedent(
        """
        Indice
        1. Ambito di applicazione .......... 3
        1.1 Requisiti generali
        .......... 4
        Capitolo 7 Requisiti operativi 42
        Questa riga non e un indice 42
        """
    ).strip()
    page1.insert_text((72, 72), text1, fontsize=12)

    page2 = doc.new_page()
    page2.insert_text(
        (72, 72),
        "Premessa\nTesto introduttivo di supporto.\n",
        fontsize=12,
    )

    page3 = doc.new_page()
    page3.insert_text((72, 72), "1. Ambito di applicazione\nContenuto sezione 1.\n", fontsize=12)

    page4 = doc.new_page()
    page4.insert_text((72, 72), "1.1 Requisiti generali\nContenuto sezione 1.1.\n", fontsize=12)

    page5 = doc.new_page()
    page5.insert_text((72, 72), "Capitolo 7 Requisiti operativi\nContenuto capitolo 7.\n", fontsize=12)

    doc.save(pdf_path)
    doc.close()


def run_parser_indice(repo_path: Path, pdf_path: Path) -> dict:
    runner = textwrap.dedent(
        f"""
        import json
        import sys
        import time
        sys.path.insert(0, {str(repo_path / "src/be/src")!r})
        start = time.perf_counter()
        from lex_package.parsing_utils.parser_indice import parser_indice
        result = parser_indice({str(pdf_path)!r})
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        payload = {{
            "pdf": {pdf_path.name!r},
            "elapsed_ms": elapsed_ms,
            "entry_count": len(result),
            "entries": result,
        }}
        print(json.dumps(payload, ensure_ascii=False))
        """
    )
    completed = subprocess.run(
        [str(VENV_PYTHON), "-c", runner],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout.strip())


def summarize_case(case: dict) -> dict:
    entries = case["entries"]
    return {
        "pdf": case["pdf"],
        "elapsed_ms": case["elapsed_ms"],
        "entry_count": case["entry_count"],
        "titles": [entry["titolo"] for entry in entries[:10]],
        "entries": entries,
    }


def compare_results(main_results: list[dict], branch_results: list[dict]) -> list[dict]:
    by_name_main = {item["pdf"]: item for item in main_results}
    by_name_branch = {item["pdf"]: item for item in branch_results}
    rows: list[dict] = []
    for pdf_name in sorted(by_name_branch):
        main_case = by_name_main[pdf_name]
        branch_case = by_name_branch[pdf_name]
        rows.append(
            {
                "pdf": pdf_name,
                "main_elapsed_ms": main_case["elapsed_ms"],
                "branch_elapsed_ms": branch_case["elapsed_ms"],
                "delta_elapsed_ms": round(branch_case["elapsed_ms"] - main_case["elapsed_ms"], 3),
                "main_entry_count": main_case["entry_count"],
                "branch_entry_count": branch_case["entry_count"],
                "main_titles": [entry["titolo"] for entry in main_case["entries"]],
                "branch_titles": [entry["titolo"] for entry in branch_case["entries"]],
            }
        )
    return rows


def build_markdown_report(main_results: list[dict], branch_results: list[dict], comparison: list[dict]) -> str:
    lines = [
        "# Parser Comparison",
        "",
        "## Scope",
        "- confronto tra `main` e `feat/parser-quality-performance` sul parser indice",
        "- stesso interprete Python e stesso ambiente virtuale",
        "- stessi PDF di input",
        "",
        "## Input",
    ]
    for item in branch_results:
        lines.append(f"- `{item['pdf']}`")

    lines.extend(
        [
            "",
            "## Summary Table",
            "",
            "| PDF | main ms | branch ms | delta ms | main entries | branch entries |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in comparison:
        lines.append(
            f"| `{row['pdf']}` | {row['main_elapsed_ms']} | {row['branch_elapsed_ms']} | {row['delta_elapsed_ms']} | {row['main_entry_count']} | {row['branch_entry_count']} |"
        )

    lines.append("")
    lines.append("## Detailed Comparison")
    lines.append("")
    for row in comparison:
        lines.append(f"### {row['pdf']}")
        lines.append(f"- main entries: {row['main_entry_count']}")
        lines.append(f"- branch entries: {row['branch_entry_count']}")
        lines.append(f"- main time: {row['main_elapsed_ms']} ms")
        lines.append(f"- branch time: {row['branch_elapsed_ms']} ms")
        lines.append(f"- delta: {row['delta_elapsed_ms']} ms")
        lines.append(f"- main titles: {row['main_titles']}")
        lines.append(f"- branch titles: {row['branch_titles']}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    ensure_dir(REPORTS_DIR)
    ensure_dir(ASSETS_DIR)

    main_worktree = Path("/tmp/SunnitAI-BE-main")
    archive_path = Path("/tmp/SunnitAI-BE-main.tar")
    if main_worktree.exists():
        shutil.rmtree(main_worktree)
    if archive_path.exists():
        archive_path.unlink()

    main_worktree.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", "main", "-o", str(archive_path)],
        check=True,
    )
    with tarfile.open(archive_path) as tar:
        tar.extractall(main_worktree)

    try:
        fixture_pdf = ASSETS_DIR / "generated_index_case.pdf"
        generate_fixture_pdf(fixture_pdf)

        repo_test_pdfs = [
            REPO_ROOT / "src/be/requirement_extration/tests/test_data/NEW_1to5.pdf",
            REPO_ROOT / "src/be/requirement_extration/tests/test_data/OLD_1to5.pdf",
            fixture_pdf,
        ]

        main_results = [summarize_case(run_parser_indice(main_worktree, pdf)) for pdf in repo_test_pdfs]
        branch_results = [summarize_case(run_parser_indice(REPO_ROOT, pdf)) for pdf in repo_test_pdfs]
        comparison = compare_results(main_results, branch_results)

        (REPORTS_DIR / "results_main.json").write_text(
            json.dumps(main_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (REPORTS_DIR / "results_branch.json").write_text(
            json.dumps(branch_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (REPORTS_DIR / "comparison_report.md").write_text(
            build_markdown_report(main_results, branch_results, comparison),
            encoding="utf-8",
        )
    finally:
        if archive_path.exists():
            archive_path.unlink()
        if main_worktree.exists():
            shutil.rmtree(main_worktree)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
