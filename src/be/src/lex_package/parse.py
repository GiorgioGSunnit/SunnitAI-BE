import json
import re
from .parsing_utils.parser_regolamento import parser as parser_documento
from .parsing_utils.parser_articolo import parser_articolo
from .parsing_utils.parser_indice import parser_indice
from .parsing_utils.parser_capitolo import parser_capitolo
from .parsing_utils.parser_docs_senza_indice import parser_contenuto
from .parsing_utils.parser_banca import detect_start_page, parser_pdf
from .parsing_utils.parser_boe import parser_boe, looks_like_boe_document
from .parsing_utils.parser_gazzetta_ue import parser_gazzetta_ue, looks_like_gazzetta_ue_document
from .parsing_utils.parser_annex_tabular import parser_annex_tabular, looks_like_annex_tabular_document
from .parsing_utils.document_profiler import profile_document
from pathlib import Path


def _save_output(articoli, indice, output_file_path_str):
    """Save parsed output to JSON file if path is provided."""
    contenuto_json = {"articoli": articoli}
    contenuto_json.update({k: v for k, v in [("indice", indice)] if v})

    if output_file_path_str:
        output_file = Path(output_file_path_str)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(contenuto_json, f, ensure_ascii=False, indent=2)

    return contenuto_json


def parse(pdf_path, pdf_name, output_file_path_str: str | None = None):
    # ── Phase 1: Profile the document (single-pass feature extraction) ──
    profile = profile_document(pdf_path, pdf_name)
    parser_type = profile.detected_type

    print(f"[INFO] Document profiler: detected_type={parser_type}, confidence={profile.confidence:.2f}")
    print(f"[INFO] Scores: {profile.scores}")

    articoli: list[dict] = []
    indice: list[dict] = []

    # ── Phase 2: Route to the selected parser ──

    if parser_type == "boe":
        print("[INFO]: Uso stream BOE parser_boe")
        articoli = parser_boe(f"{pdf_path}")
        if articoli:
            _save_output(articoli, indice, output_file_path_str)
            return articoli

    elif parser_type == "annex_tabular":
        print("[INFO]: Uso stream Annex Tabular parser_annex_tabular")
        articoli = parser_annex_tabular(f"{pdf_path}")
        if articoli:
            _save_output(articoli, indice, output_file_path_str)
            return articoli

    elif parser_type == "gazzetta_ue":
        print("[INFO]: Uso stream Gazzetta UE parser_gazzetta_ue")
        articoli = parser_gazzetta_ue(f"{pdf_path}")
        if articoli:
            _save_output(articoli, indice, output_file_path_str)
            return articoli

    elif parser_type == "banca":
        effective_start_page = profile.banca_start_page if profile.banca_start_page is not None else 1
        print(
            f"[INFO]: Uso stream banca parser_pdf (start_page={effective_start_page})"
        )
        articoli = parser_pdf(f"{pdf_path}", effective_start_page)

    # ── Phase 3: Fallback chain (regolamento / indice / free_form) ──

    if not articoli:
        if parser_type == "indice":
            print("[INFO]: Profiler detected indice, uso parser_capitolo")
            indice = parser_indice(f"{pdf_path}")
            if indice:
                articoli = parser_capitolo(f"{pdf_path}", indice)

        if not articoli:
            print("[INFO]: uso parser_documento come fallback")
            p: list[dict] = parser_documento(f"{pdf_path}")[:]
            articoli = [
                {
                    **a,
                    "contenuto_parsato": [
                        {**x, "titolo_articolo": a["titolo"]}
                        for x in parser_articolo(a["contenuto"])
                    ],
                }
                for a in p
            ]
            if articoli:
                for a in articoli:
                    for c in a["contenuto_parsato"]:
                        c["contenuto_parsato_2"] = parser_articolo(c["contenuto"])
            else:
                indice = parser_indice(f"{pdf_path}")
                if indice:
                    print("[INFO]: Trovato indice, uso parser_capitolo")
                    articoli = parser_capitolo(f"{pdf_path}", indice)
                else:
                    print("[INFO]: Nessun indice trovato, uso parser_contenuto come fallback")
                    articoli = parser_contenuto(f"{pdf_path}")

    # ── Save and return ──
    _save_output(articoli, indice, output_file_path_str)
    return articoli
