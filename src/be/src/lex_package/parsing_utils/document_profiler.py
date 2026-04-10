"""
Document profiler for parser routing.

Performs a single-pass analysis of a PDF to extract structural features,
then selects the best parser based on accumulated scores.
Saves the profile as JSON for debugging and traceability.
"""

import re
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

import fitz  # PyMuPDF

from .parser_banca import detect_start_page


_SRCDIR = Path(__file__).resolve().parents[2]

# BOE
_BOE_HEADER_RE = re.compile(
    r"B\.?\s*O\.?\s*(?:del)?\s*E\.?\s*[—\-–]\s*[Nn]úm\.?\s*\d+",
    re.IGNORECASE,
)
_BOLETIN_RE = re.compile(
    r"Bolet[ií]n\s+Oficial\s+del\s+Estado", re.IGNORECASE,
)
_SPANISH_DATE_RE = re.compile(
    r"\d{1,2}\s+(?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?\d{4}",
    re.IGNORECASE,
)

# Gazzetta UE
_GAZZETTA_HEADER_RE = re.compile(
    r"Gazzetta\s+ufficiale\s+(?:dell[ae]?\s+)?(?:Unione\s+europea|Comunit[àa]\s+europe[ea])",
    re.IGNORECASE,
)
_OFFICIAL_JOURNAL_RE = re.compile(
    r"Official\s+Journal", re.IGNORECASE,
)
_EU_DOC_TYPE_RE = re.compile(
    r"(?:REGOLAMENTO|DIRETTIVA|DECISIONE|REGULATION|DIRECTIVE|DECISION)"
    r"\s+(?:DELEGATO\s+|DI\s+ESECUZIONE\s+|IMPLEMENTING\s+|DELEGATED\s+)?"
    r"(?:\((?:UE|CE|CEE|EU|EC)\)\s+)?(?:N\.\s*)?\d+/\d+",
    re.IGNORECASE,
)
_PAGE_REF_RE = re.compile(r"L\s+\d{1,4}\s*/\s*\d{1,4}")

# Annex Tabular
_COL_LETTERS = {"A", "B", "C", "D", "E", "F", "G"}

# Index / TOC
_INDICE_ENTRY_RE = re.compile(
    r"\d{1,4}(?:\.\d{1,4})*\.?\s+.+?[.\u2026\u00B7\u2022•·∙]{2,}\s*\d{1,4}\b"
)


@dataclass
class DocumentProfile:
    pdf_path: str = ""
    pdf_name: str = ""
    page_count: int = 0
    has_boe_header: bool = False
    has_boletin_text: bool = False
    has_gazzetta_header: bool = False
    has_official_journal: bool = False
    has_l_page_ref: bool = False
    has_banca_ditalia: bool = False
    has_disposizioni: bool = False
    has_articolo_pattern: bool = False
    has_article_pattern: bool = False
    has_considerando: bool = False
    has_annex_keyword: bool = False
    has_indice_keyword: bool = False
    has_indice_entries: bool = False
    has_eu_doc_type: bool = False
    has_eu_institution: bool = False
    has_spanish_date: bool = False
    has_ministry_keywords: bool = False
    has_disposicion_numbers: bool = False
    has_data_format_indicators: bool = False
    has_parte_structure: bool = False
    has_capitolo_structure: bool = False
    has_sezione_structure: bool = False
    pages_with_tables: int = 0
    pages_with_ag_header: int = 0
    table_page_ratio: float = 0.0
    banca_start_page: int | None = None
    filename_boe: bool = False
    filename_gazzetta: bool = False
    filename_annex: bool = False
    filename_banca: bool = False
    detected_type: str = ""
    confidence: float = 0.0
    scores: dict = field(default_factory=dict)


def _is_ag_header_row(row):
    if not row:
        return False
    non_empty = [c.strip().upper() for c in row if c and c.strip()]
    letters = {c for c in non_empty if len(c) == 1 and c in _COL_LETTERS}
    return len(letters) >= 4


def _extract_features(doc, pdf_name):
    profile = DocumentProfile(page_count=doc.page_count)
    name = (pdf_name or "").lower()
    profile.filename_boe = any(t in name for t in ("boe", "boletín", "boletin", "estado"))
    profile.filename_gazzetta = any(t in name for t in ("gazzetta", "gu_", "oj_"))
    profile.filename_annex = any(t in name for t in ("emcs", "annex_tab", "allegato_tab"))
    profile.filename_banca = any(t in name for t in ("banca", "disposizioni", "avc", "bdi"))

    head_pages = min(3, doc.page_count)
    head_text = "\n".join(doc[i].get_text() for i in range(head_pages))
    extended_pages = min(8, doc.page_count)
    extended_lower = "\n".join(doc[i].get_text() for i in range(extended_pages)).lower()
    scan_pages = min(15, doc.page_count)
    broader_text = "\n".join(doc[i].get_text() for i in range(scan_pages))

    profile.has_boe_header = bool(_BOE_HEADER_RE.search(head_text))
    profile.has_boletin_text = bool(_BOLETIN_RE.search(head_text))
    profile.has_spanish_date = bool(_SPANISH_DATE_RE.search(head_text))
    profile.has_ministry_keywords = bool(re.search(r"MINISTERIO\s+DE\s+", head_text))
    disp_matches = re.findall(r"^\s*\d{4,5}\s+[A-ZÁÉÍÓÚÑÜ]", head_text, re.MULTILINE)
    profile.has_disposicion_numbers = len(disp_matches) >= 2

    profile.has_gazzetta_header = bool(_GAZZETTA_HEADER_RE.search(head_text))
    profile.has_official_journal = bool(_OFFICIAL_JOURNAL_RE.search(head_text))
    profile.has_l_page_ref = bool(_PAGE_REF_RE.search(head_text))
    profile.has_eu_doc_type = bool(_EU_DOC_TYPE_RE.search(head_text))
    profile.has_eu_institution = bool(
        re.search(r"(?:CONSIGLIO|PARLAMENTO\s+EUROPEO|COMMISSIONE|COUNCIL|PARLIAMENT|COMMISSION)",
                  head_text, re.IGNORECASE)
    )
    profile.has_articolo_pattern = bool(re.search(r"\bArticolo\s+\d+\b", head_text))
    profile.has_article_pattern = bool(re.search(r"\bArticle\s+\d+\b", head_text))
    profile.has_considerando = bool(re.search(r"\bconsiderando\b", head_text, re.IGNORECASE))

    profile.has_annex_keyword = bool(re.search(r"\bANNEX\b|\bALLEGATO\b", head_text, re.IGNORECASE))
    profile.has_data_format_indicators = bool(re.search(r"\ban?\.\.\d+\b", broader_text))
    profile.has_banca_ditalia = ("banca d'italia" in extended_lower or "banca d\u2019italia" in extended_lower)
    profile.has_disposizioni = bool(re.search(r"\bdisposizioni\b", extended_lower))
    profile.has_parte_structure = bool(re.search(r"\bparte\s+[ivxlc0-9]+\b", extended_lower))
    profile.has_capitolo_structure = bool(re.search(r"\bcapitolo\s+\d+\b", extended_lower))
    profile.has_sezione_structure = bool(re.search(r"\bsezione\s+[ivxlc0-9]+\b", extended_lower))

    indice_text = "\n".join(doc[i].get_text() for i in range(min(2, doc.page_count)))
    profile.has_indice_keyword = bool(re.search(r"\bindice\b", indice_text, re.IGNORECASE))
    profile.has_indice_entries = bool(_INDICE_ENTRY_RE.search(indice_text))

    sampled_pages = set(range(scan_pages))
    if doc.page_count > 15:
        mid = doc.page_count // 2
        sampled_pages.update([mid - 1, mid, mid + 1])

    for i in sampled_pages:
        if i >= doc.page_count:
            continue
        tabs = doc[i].find_tables()
        if tabs.tables:
            profile.pages_with_tables += 1
            for table in tabs.tables:
                data = table.extract()
                if data and _is_ag_header_row(data[0]):
                    profile.pages_with_ag_header += 1
                    break

    if sampled_pages:
        profile.table_page_ratio = profile.pages_with_tables / len(sampled_pages)

    return profile


def _compute_scores(profile: DocumentProfile) -> dict[str, int]:
    scores = {}
    s = 0
    if profile.has_boe_header:
        s += 3
    if profile.has_boletin_text:
        s += 3
    if profile.has_disposicion_numbers:
        s += 2
    if profile.has_ministry_keywords:
        s += 1
    if profile.has_spanish_date:
        s += 1
    scores["boe"] = s

    s = 0
    if profile.has_official_journal or profile.has_gazzetta_header:
        s += 2
    if profile.has_l_page_ref:
        s += 1
    if profile.pages_with_ag_header >= 3:
        s += 3
    elif profile.pages_with_ag_header >= 1:
        s += 2
    if profile.table_page_ratio > 0.6:
        s += 1
    if profile.has_data_format_indicators:
        s += 1
    if profile.has_annex_keyword:
        s += 1
    scores["annex_tabular"] = s

    s = 0
    if profile.has_gazzetta_header or profile.has_official_journal:
        s += 3
    if profile.has_eu_doc_type:
        s += 2
    if profile.has_articolo_pattern or profile.has_article_pattern:
        s += 1
    if profile.has_eu_institution:
        s += 1
    if profile.has_l_page_ref:
        s += 1
    if profile.has_considerando:
        s += 1
    scores["gazzetta_ue"] = s

    s = 0
    if profile.has_banca_ditalia:
        s += 2
    if profile.has_disposizioni:
        s += 1
    if profile.has_parte_structure:
        s += 1
    if profile.has_capitolo_structure:
        s += 1
    if profile.has_sezione_structure:
        s += 1
    scores["banca"] = s
    return scores


_THRESHOLDS = {
    "boe": (3, 10),
    "annex_tabular": (5, 9),
    "gazzetta_ue": (4, 9),
    "banca": (3, 6),
}

_PRIORITY = ["boe", "annex_tabular", "gazzetta_ue", "banca"]


def select_parser(profile: DocumentProfile) -> str:
    scores = profile.scores
    filename_matches = []
    if profile.filename_boe:
        filename_matches.append("boe")
    if profile.filename_annex:
        filename_matches.append("annex_tabular")
    if profile.filename_gazzetta:
        filename_matches.append("gazzetta_ue")
    if profile.filename_banca:
        filename_matches.append("banca")
    if len(filename_matches) == 1:
        return filename_matches[0]

    for parser_type in _PRIORITY:
        threshold, _ = _THRESHOLDS[parser_type]
        if scores.get(parser_type, 0) >= threshold:
            if parser_type == "banca":
                if profile.banca_start_page is not None or scores["banca"] >= threshold:
                    return "banca"
                continue
            return parser_type

    if profile.banca_start_page is not None:
        return "banca"
    if profile.has_articolo_pattern or profile.has_article_pattern:
        return "regolamento"
    if profile.has_indice_keyword and profile.has_indice_entries:
        return "indice"
    return "free_form"


def profile_document(pdf_path: str, pdf_name: str = "") -> DocumentProfile:
    if not pdf_name:
        pdf_name = Path(pdf_path).name

    doc = fitz.open(pdf_path)
    try:
        profile = _extract_features(doc, pdf_name)
    finally:
        doc.close()

    profile.pdf_path = str(pdf_path)
    profile.pdf_name = pdf_name
    try:
        profile.banca_start_page = detect_start_page(str(pdf_path))
    except Exception:
        profile.banca_start_page = None

    profile.scores = _compute_scores(profile)
    profile.detected_type = select_parser(profile)
    max_scores = {k: v for k, (_, v) in _THRESHOLDS.items()}
    detected = profile.detected_type
    if detected in max_scores and max_scores[detected] > 0:
        profile.confidence = min(1.0, profile.scores[detected] / max_scores[detected])
    elif detected in ("regolamento", "indice", "free_form"):
        profile.confidence = 0.5
    else:
        profile.confidence = 0.0

    _save_profile(profile)
    return profile


def _save_profile(profile: DocumentProfile):
    log_dir = Path(_SRCDIR / "out_parser")
    log_dir.mkdir(parents=True, exist_ok=True)
    profile_path = os.path.join(log_dir, "document_profile.json")
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(asdict(profile), f, ensure_ascii=False, indent=2)
