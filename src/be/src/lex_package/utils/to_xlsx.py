from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, List, Dict

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

__all__ = ["write_records_to_xlsx"]


def _validate_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return *records* as list while verifying the expected structure.

    Raises
    ------
    TypeError
        If *records* is not an iterable of dicts.
    """
    if not isinstance(records, Iterable):
        raise TypeError("records must be an iterable of dictionaries")
    records_list = list(records)
    if not all(isinstance(rec, dict) for rec in records_list):
        raise TypeError("every item in records must be a dict")
    return records_list  # all good


def write_records_to_xlsx(
    records: Iterable[Dict[str, Any]],
    output_path: str | Path,
    sheet_name: str = "Sheet1",
) -> None:
    """Write *records* to an Excel file at *output_path*.

    Parameters
    ----------
    records:
        Iterable of dictionaries representing rows.
    output_path:
        Destination XLSX file. Existing files are overwritten.
    sheet_name:
        Optional name for the sheet. Defaults to ``"Sheet1"``.

    Examples
    --------
    >>> recs = [{"name": "Alice", "age": 30}, {"name": "Bob"}]
    >>> write_records_to_xlsx(recs, "people.xlsx", sheet_name="People")
    """
    recs = _validate_records(records)
    df = pd.DataFrame.from_records(recs)
    df_clean = sanitize_df(df)
    df_clean.to_excel(
        Path(output_path), sheet_name=sheet_name, index=False, engine="openpyxl"
    )


def beautify_xlsx_confronto_attuativo(
    sheet_name: str = "Sheet1",
) -> None:
    """
    Apre il file "../out_flat/out_schema_attuativo/confronto.xlsx",
    riordina le colonne secondo le specifiche,
    rinomina alcune intestazioni e salva il risultato in output_path
    """

    _SRCDIR = Path(__file__).resolve().parents[2]

    # 1) Leggi il foglio di lavoro in un DataFrame
    input_path = Path(_SRCDIR / "out_flat/out_schema_attuativo")

    # Ensure directory exists
    input_path.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(input_path / "confronto_attuativo.xlsx", sheet_name=sheet_name)

    # 2) Definisci l'ordine esatto delle colonne da mantenere
    desired_columns = [
        "Pagina",  # colonna 1
        "Titolo Articolo",  # colonna 2
        "Articolo",  # colonna 3
        "Identificativo Comma",  # colonna 4 (da rinominare in "Comma")
        "Identificativo Sottocomma",  # colonna 5 (da rinominare in "Paragrafo")
        "Contenuto Articolo",  # colonna 6
        "Contenuto Comma",  # colonna 7
        "Contenuto Sottocomma",  # colonna 8
        "Pattern Type",  # colonna 9
        "Riferimento del Sottocomma - Nome Documento",  # colonna 10 (da rinominare in "Rif. Documento")
        "Riferimento del Sottocomma - Codice Documento",  # colonna 11 (da rinominare in "Rif. Codice")
        "Riferimento del Sottocomma - Tipo match trovato",  # colonna 12 (da rinominare in "Rif. Tipo Match")
        "Riferimento del Sottocomma - Identificativo Articolo",  # colonna 13 (da rinominare in "Rif.Articolo")
        "Riferimento del Sottocomma - Titolo Articolo",  # colonna 14 (da rinominare in "Rif. Titolo Articolo")
        "Riferimento del Sottocomma - Identificativo Comma",  # colonna 15 (da rinominare in "Rif. Comma")
        "Riferimento del Sottocomma - Contenuto",  # colonna 16 (da rinominare in "Rif. Contenuto")
        "Riferimento del Sottocomma - Motivazione",  # colonna 17 (da rinominare in "Dettaglio")
        "Riferimento del Sottocomma - Relazione Contenuto",  # colonna 18 (da rinominare in "Rif. Relazione")
        "Tipo",  # colonna 19
        "Requirement",  # colonna 20
    ]

    # 3) Mappa per rinominare le colonne
    rename_map = {
        "Identificativo Comma": "Comma",
        "Identificativo Sottocomma": "Paragrafo",
        "Riferimento del Sottocomma - Nome Documento": "Rif. Documento",
        "Riferimento del Sottocomma - Codice Documento": "Rif. Codice",
        "Riferimento del Sottocomma - Tipo match trovato": "Rif. Tipo Match",
        "Riferimento del Sottocomma - Identificativo Articolo": "Rif.Articolo",
        "Riferimento del Sottocomma - Titolo Articolo": "Rif. Titolo Articolo",
        "Riferimento del Sottocomma - Identificativo Comma": "Rif. Comma",
        "Riferimento del Sottocomma - Contenuto": "Rif. Contenuto",
        "Riferimento del Sottocomma - Motivazione": "Dettaglio",
        "Riferimento del Sottocomma - Relazione Contenuto": "Rif. Relazione",
    }

    # 4) Verifica che tutte le colonne richieste esistano nel DataFrame
    missing = [col for col in desired_columns if col not in df.columns]
    if missing:
        raise KeyError(f"Quelle colonne non esistono nel file sorgente: {missing}")

    # 5) Seleziona solo le colonne nell'ordine voluto
    df_reordered = df[desired_columns].copy()

    # 6) Rinomina le colonne secondo la mappa
    df_reordered.rename(columns=rename_map, inplace=True)

    # 7) Salva il nuovo XLSX nel percorso di output specificato
    output_path = Path(input_path)
    df_reordered.to_excel(
        output_path / "beautify_xlsx_confronto_attuativo.xlsx",
        sheet_name=sheet_name,
        index=False,
    )


# regex per caratteri di controllo ASCII non ammessi in Excel
_ILLEGAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

def sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Rimuove caratteri non validi per Excel dalle stringhe di *df*.

    Evita import interni a pandas / openpyxl usando una regex costante che
    copre i caratteri di controllo 0x00–0x1F esclusi TAB, LF e CR.
    """

    return df.replace(_ILLEGAL_CHARS_RE, "", regex=True)
