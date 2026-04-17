import json
import re
from pathlib import Path

from .parsing_utils.parser_regolamento import parser as parser_documento
from .parsing_utils.parser_articolo import parser_articolo
from .parsing_utils.parser_indice import parser_indice
from .parsing_utils.parser_capitolo import parser_capitolo
from .parsing_utils.parser_docs_senza_indice import parser_contenuto
from .parsing_utils.parser_banca import detect_start_page, parser_pdf
from .parsing_utils.parser_boe import parser_boe
from .parsing_utils.parser_gazzetta_ue import parser_gazzetta_ue
from .parsing_utils.parser_annex_tabular import parser_annex_tabular
from .parsing_utils.parser_general import parser_general, parts_to_articoli
from .parsing_utils.document_profiler import profile_document
from .parsing_utils.document_part import DocumentPart


# ─── Constants ────────────────────────────────────────────────────────────────

_SPLIT_THRESHOLD = 10_000
_SPLIT_MAX = 12_000
_SENTENCE_END_RE = re.compile(r"[.!?]\s")


# ─── Output helpers ───────────────────────────────────────────────────────────

def _save_output(
    articoli: list[dict],
    indice: list[dict],
    output_file_path_str: str | None,
    parts: list[dict] | None = None,
    template_meta: dict | None = None,
) -> dict:
    """Serialise parsed output to JSON and return the dict."""
    contenuto_json: dict = {"articoli": articoli}

    if indice:
        contenuto_json["indice"] = indice
    if parts is not None:
        contenuto_json["parts"] = parts
    if template_meta:
        contenuto_json["template_meta"] = template_meta

    if output_file_path_str:
        output_file = Path(output_file_path_str)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(contenuto_json, f, ensure_ascii=False, indent=2)

    return contenuto_json


# ─── Articoli → parts converter ───────────────────────────────────────────────

def _articoli_to_parts(articoli: list[dict]) -> list[dict]:
    """
    Convert the legacy articoli list (produced by all specialised parsers) into
    a flat DocumentPart list for Part-B AI enrichment.

    Long paragraphs are split at sentence boundaries to respect the
    ~1 000–1 200 character target size.  Each resulting part stores the
    full positional context (section title, page, hierarchy) and the
    sibling_of pointer that links consecutive parts in the same section.
    """
    parts: list[dict] = []
    part_id = 0

    for articolo in articoli:
        section_title: str = articolo.get("titolo") or ""
        page: int = articolo.get("page") or 0
        hierarchy: list[str] = _build_hierarchy(articolo)
        last_part_id_in_section: int | None = None

        # Prefer fine-grained contenuto_parsato if available
        paragraphs: list[tuple[str, int]] = []
        for p in articolo.get("contenuto_parsato", []):
            paragraphs.append((p.get("contenuto") or "", p.get("page") or page))
        if not paragraphs:
            paragraphs = [(articolo.get("contenuto") or "", page)]

        buf = ""
        buf_page = page

        def _emit(text: str, pg: int) -> None:
            nonlocal part_id, last_part_id_in_section
            text = text.strip()
            if not text:
                return
            part_id += 1
            parts.append(
                DocumentPart(
                    part_id=part_id,
                    sibling_of=last_part_id_in_section,
                    section_title=section_title,
                    page=pg,
                    bbox=[0.0, 0.0, 0.0, 0.0],
                    content=text,
                    char_count=len(text),
                    font_name=None,
                    font_size=None,
                    place={
                        "section_title": section_title,
                        "page": pg,
                        "bbox": [0.0, 0.0, 0.0, 0.0],
                        "hierarchy": list(hierarchy),
                    },
                ).to_dict()
            )
            last_part_id_in_section = part_id

        for text, pg in paragraphs:
            if not text.strip():
                continue
            buf = (buf + " " + text).strip() if buf else text.strip()
            buf_page = pg

            while len(buf) > _SPLIT_THRESHOLD:
                search_end = min(_SPLIT_MAX, len(buf))
                m = _SENTENCE_END_RE.search(buf, _SPLIT_THRESHOLD, search_end)
                cut = (m.start() + 1) if m else search_end
                _emit(buf[:cut], buf_page)
                buf = buf[cut:].strip()

        if buf:
            _emit(buf, buf_page)

    return parts


def _build_hierarchy(articolo: dict) -> list[str]:
    path: list[str] = []
    for key in ("parte", "titolo_parte", "capitolo", "sezione", "titolo"):
        val = articolo.get(key)
        if val and str(val).strip():
            path.append(str(val).strip())
    return path


# ─── Main parse entry point ───────────────────────────────────────────────────

def parse(
    pdf_path,
    pdf_name,
    output_file_path_str: str | None = None,
    template_hint: str | None = None,
) -> list[dict]:
    """
    Parse *pdf_path* and return the articoli list.

    Args:
        pdf_path: Path to the PDF file.
        pdf_name: Document name (used for filename-based template scoring).
        output_file_path_str: If given, write JSON output to this path.
        template_hint: Optional user-supplied template name (e.g. "boe").
            When provided, template scoring is skipped and the named parser
            is used directly (if the hint matches a known template).
    """
    # ── Phase 1: Profile ──────────────────────────────────────────────────
    profile = profile_document(pdf_path, pdf_name, template_hint=template_hint)
    parser_type = profile.detected_type

    print(f"[INFO] Document profiler: detected_type={parser_type}, confidence={profile.confidence:.2f}")
    print(f"[INFO] Scores: {profile.scores}")
    if template_hint:
        print(f"[INFO] Template hint supplied: '{template_hint}'")

    articoli: list[dict] = []
    indice: list[dict] = []
    parts: list[dict] | None = None

    # ── Phase 2: Route to specialised parser ──────────────────────────────

    if parser_type == "boe":
        print("[INFO] Using parser_boe")
        articoli = parser_boe(str(pdf_path))

    elif parser_type == "annex_tabular":
        print("[INFO] Using parser_annex_tabular")
        articoli = parser_annex_tabular(str(pdf_path))

    elif parser_type == "gazzetta_ue":
        print("[INFO] Using parser_gazzetta_ue")
        articoli = parser_gazzetta_ue(str(pdf_path))

    elif parser_type == "banca":
        start = profile.banca_start_page if profile.banca_start_page is not None else 1
        print(f"[INFO] Using parser_pdf/banca (start_page={start})")
        articoli = parser_pdf(str(pdf_path), start)

    elif parser_type == "general":
        print("[INFO] Using parser_general (box/sibling model)")
        parts = parser_general(str(pdf_path))
        articoli = parts_to_articoli(parts)
        _save_output(articoli, indice, output_file_path_str, parts=parts,
                     template_meta=profile.template_meta)
        return articoli

    # ── Phase 3: Fallback chain for regolamento / indice ──────────────────

    if not articoli:
        if parser_type == "indice":
            print("[INFO] Profiler detected indice, using parser_capitolo")
            indice = parser_indice(str(pdf_path))
            if indice:
                articoli = parser_capitolo(str(pdf_path), indice)

        if not articoli:
            print("[INFO] Using parser_documento (regolamento) as fallback")
            p: list[dict] = parser_documento(str(pdf_path))[:]
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
                indice = parser_indice(str(pdf_path))
                if indice:
                    print("[INFO] Found indice, using parser_capitolo")
                    articoli = parser_capitolo(str(pdf_path), indice)
                else:
                    print("[INFO] No index found, using parser_contenuto as final fallback")
                    articoli = parser_contenuto(str(pdf_path))

    # ── Phase 4: Convert articoli → parts for Part-B readiness ───────────
    if articoli:
        parts = _articoli_to_parts(articoli)

    # ── Save and return ───────────────────────────────────────────────────
    _save_output(articoli, indice, output_file_path_str,
                 parts=parts, template_meta=profile.template_meta)
    return articoli
