#!/usr/bin/env python3
"""CLI: delega a lex_package.utils.confronto_xlsx_vista (aggiungere src/be/src a PYTHONPATH)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "be" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lex_package.utils.confronto_xlsx_vista import run_cli_main

if __name__ == "__main__":
    run_cli_main()
