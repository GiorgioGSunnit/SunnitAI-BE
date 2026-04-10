"""
Parser for BOE (Boletin Oficial del Estado) documents.
"""

import re
import fitz  # PyMuPDF
import json
import os
from collections import Counter
from pathlib import Path


_SRCDIR = Path(__file__).resolve().parents[2]
_BOE_HEADER_RE = re.compile(
    r"B\.?\s*O\.?\s*(?:del)?\s*E\.?\s*[—\-–]\s*[Nn]úm\.?\s*\d+",
    re.IGNORECASE,
)
_DISPOSICION_RE = re.compile(
    r"""
    ^\s*(?P<numero>\d{4,5})\s+
    (?P<tipo>
        (?:REAL\s+DECRETO|DECRETO|LEY|ORDEN|RESOLUCI[OÓ]N|
        CORRECCI[OÓ]N\s+DE\s+ERRORES|CIRCULAR|ACUERDO|
        ANUNCIO|EDICTO|SENTENCIA|AUTO|CONVENIO|
        INSTRUCCI[OÓ]N|REGLAMENTO|DIRECTIVA)
    )
    \s+
    (?P<titulo>.+)
    """,
    re.VERBOSE | re.IGNORECASE | re.DOTALL,
)
_DISPOSICION_SIMPLE_RE = re.compile(
    r"^\s*(?P<numero>\d{4,5})\s+(?P<titulo>[A-ZÁÉÍÓÚÑÜ].+)",
    re.DOTALL,
)
_PAGE_NUM_RE = re.compile(r"^\s*\d{1,5}\s*$")
_DATE_RE = re.compile(
    r"\d{1,2}\s+(?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?\d{4}",
    re.IGNORECASE,
)


def identify_repeated_lines_boe(doc, min_repeats=3):
    line_counts = Counter()
    for page in doc:
        blocks = page.get_text("blocks")
        height = page.rect.height
        for block in blocks:
            y0 = block[1]
            text = block[4].strip()
            if not text or len(text) < 3:
                continue
            if y0 < height * 0.10 or y0 > height * 0.90:
                line_counts[text] += 1
    return {line for line, count in line_counts.items() if count >= min_repeats}


def _is_ministry_header(text, spans=None):
    stripped = text.strip()
    if not stripped:
        return False
    alpha_chars = [c for c in stripped if c.isalpha()]
    if not alpha_chars:
        return False
    upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
    ministry_keywords = (
        "MINISTERIO", "ADMINISTRACI", "JEFATURA", "PRESIDENCIA",
        "TRIBUNAL", "CONSEJO", "DIRECCI", "SECRETAR", "COMUNIDAD",
        "DIPUTACI", "AYUNTAMIENTO", "UNIVERSIDAD", "BANCO DE ESPAÑA", "COMISI",
    )
    has_keyword = any(kw in stripped.upper() for kw in ministry_keywords)
    is_bold = False
    if spans:
        bold_chars = sum(
            len(s.get("text", ""))
            for s in spans
            if "bold" in s.get("font", "").lower() or s.get("flags", 0) & 2**4
        )
        total_chars = sum(len(s.get("text", "")) for s in spans)
        is_bold = total_chars > 0 and bold_chars / total_chars > 0.5
    return has_keyword and (upper_ratio > 0.7 or is_bold)


def _extract_disposicion_type(text):
    tipo_keywords = [
        "REAL DECRETO-LEY", "REAL DECRETO LEGISLATIVO", "REAL DECRETO",
        "DECRETO-LEY", "DECRETO LEGISLATIVO", "DECRETO", "LEY ORGÁNICA", "LEY",
        "ORDEN", "RESOLUCIÓN", "RESOLUCION", "CORRECCIÓN DE ERRORES",
        "CORRECCION DE ERRORES", "CIRCULAR", "ACUERDO", "ANUNCIO", "EDICTO",
        "SENTENCIA", "AUTO", "CONVENIO", "INSTRUCCIÓN", "INSTRUCCION",
        "REGLAMENTO", "DIRECTIVA",
    ]
    upper_text = text.upper()
    for kw in tipo_keywords:
        if upper_text.startswith(kw):
            return kw
    return ""


def _is_skip_block(text, repeated_lines, y0, height):
    if not text.strip():
        return True
    if text.strip() in repeated_lines:
        return True
    if _PAGE_NUM_RE.match(text):
        return True
    if _BOE_HEADER_RE.search(text):
        return True
    if y0 < height * 0.05 or y0 > height * 0.95:
        return True
    return False


def parser_boe(pdf_path):
    doc = fitz.open(pdf_path)
    repeated_lines = identify_repeated_lines_boe(doc, min_repeats=2)
    debug_log = []
    disposiciones = []
    current_ministerio = ""
    current_disposicion = None

    for page_num in range(len(doc)):
        page = doc[page_num]
        height = page.rect.height
        blocks = page.get_text("blocks")
        blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
        page_dict = page.get_text("dict")
        span_map = {}
        for blk in page_dict.get("blocks", []):
            if blk.get("type") == 0:
                all_spans = []
                block_text_parts = []
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        all_spans.append(span)
                        block_text_parts.append(span.get("text", ""))
                block_text = " ".join(block_text_parts).strip()
                if block_text:
                    span_map[block_text[:100]] = all_spans

        tabs = page.find_tables()
        table_bboxes = [table.bbox for table in tabs.tables] if tabs.tables else []

        for block in blocks:
            x0, y0, x1, y1 = block[0], block[1], block[2], block[3]
            text = block[4].strip() if len(block) > 4 else ""
            block_type = block[6] if len(block) > 6 else 0
            if block_type == 1:
                continue
            if _is_skip_block(text, repeated_lines, y0, height):
                continue

            in_table = False
            for tbbox in table_bboxes:
                tx0, ty0, tx1, ty1 = tbbox
                if x0 >= tx0 - 2 and y0 >= ty0 - 2 and x1 <= tx1 + 2 and y1 <= ty1 + 2:
                    in_table = True
                    break

            spans_for_block = span_map.get(text.strip()[:100], None)
            if _is_ministry_header(text, spans_for_block):
                current_ministerio = text.strip()
                continue

            disp_match = _DISPOSICION_RE.match(text) or _DISPOSICION_SIMPLE_RE.match(text)
            if disp_match:
                numero = disp_match.group("numero")
                titulo_raw = disp_match.group("titulo").strip()
                tipo = _extract_disposicion_type(titulo_raw)
                if current_disposicion:
                    _finalize_disposicion(current_disposicion, disposiciones, debug_log)
                current_disposicion = {
                    "numero": numero,
                    "tipo_disposicion": tipo,
                    "ministerio": current_ministerio,
                    "titulo_raw": titulo_raw,
                    "page_start": page_num,
                    "text_parts": [titulo_raw],
                    "tables": [],
                }
                continue

            if current_disposicion:
                if in_table:
                    for table in tabs.tables:
                        tbbox = table.bbox
                        tx0, ty0, tx1, ty1 = tbbox
                        if x0 >= tx0 - 2 and y0 >= ty0 - 2 and x1 <= tx1 + 2 and y1 <= ty1 + 2:
                            table_data = table.extract()
                            if table_data not in current_disposicion["tables"]:
                                current_disposicion["tables"].append(table_data)
                            break
                else:
                    current_disposicion["text_parts"].append(text)

    if current_disposicion:
        _finalize_disposicion(current_disposicion, disposiciones, debug_log)
    doc.close()

    result = []
    for disp in disposiciones:
        contenuto_full = disp["contenuto"]
        contenuto_parsato = [{
            "tipo": "testo",
            "identificativo": disp["numero"],
            "titolo_articolo": disp["titulo"],
            "contenuto": contenuto_full,
        }]
        for i, table_data in enumerate(disp.get("tables", [])):
            contenuto_parsato.append({
                "tipo": "tabella",
                "identificativo": f"{disp['numero']}_tab{i+1}",
                "titolo_articolo": "",
                "contenuto": str(table_data),
            })
        result.append({
            "codicedocumento": "BOE",
            "page": disp["page_start"],
            "identificativo": disp["numero"],
            "titolo": disp["titulo"],
            "tipo_disposicion": disp.get("tipo_disposicion", ""),
            "ministerio": disp.get("ministerio", ""),
            "codicearticolo": "",
            "contenuto": contenuto_full,
            "contenuto_parsato": contenuto_parsato,
        })

    log_dir = Path(_SRCDIR / "out_parser")
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(os.path.join(log_dir, "debug_log_parseBOE.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(debug_log))
    with open(os.path.join(log_dir, "result_BOE.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def _finalize_disposicion(disp, disposiciones, debug_log):
    full_text = "\n".join(disp["text_parts"])
    titulo = disp["titulo_raw"]
    title_match = re.match(
        r"(.+?(?:de\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4})[^.]*\.)",
        titulo,
        re.DOTALL | re.IGNORECASE,
    )
    if title_match:
        titulo = title_match.group(1).strip()
    contenuto = full_text.strip()
    disp["titulo"] = titulo.replace("\n", " ").strip()
    disp["contenuto"] = contenuto.replace("\n", " ").replace("- ", "-").strip()
    disposiciones.append(disp)
    debug_log.append(f"FINALIZED #{disp['numero']}")


def looks_like_boe_document(pdf_path, pdf_name=""):
    name = (pdf_name or "").lower()
    if any(token in name for token in ("boe", "boletín", "boletin", "estado")):
        return True
    try:
        doc = fitz.open(pdf_path)
        try:
            max_pages = min(3, doc.page_count)
            head_text = "\n".join(doc[i].get_text() for i in range(max_pages))
        finally:
            doc.close()
    except Exception:
        return False
    score = 0
    if _BOE_HEADER_RE.search(head_text):
        score += 3
    if re.search(r"Bolet[ií]n\s+Oficial\s+del\s+Estado", head_text, re.IGNORECASE):
        score += 3
    disp_matches = re.findall(r"^\s*\d{4,5}\s+[A-ZÁÉÍÓÚÑÜ]", head_text, re.MULTILINE)
    if len(disp_matches) >= 2:
        score += 2
    if re.search(r"MINISTERIO\s+DE\s+", head_text):
        score += 1
    if _DATE_RE.search(head_text):
        score += 1
    return score >= 3
