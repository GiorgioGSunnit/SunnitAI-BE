"""
Parser for EU Official Journal documents (Gazzetta ufficiale dell'Unione europea).
"""

import re
import fitz  # PyMuPDF
import json
import os
from collections import Counter
from pathlib import Path


_SRCDIR = Path(__file__).resolve().parents[2]
_GAZZETTA_HEADER_RE = re.compile(
    r"Gazzetta\s+ufficiale\s+(?:dell[ae]?\s+)?(?:Unione\s+europea|Comunit[àa]\s+europe[ea])",
    re.IGNORECASE,
)
_PAGE_REF_RE = re.compile(r"[NnLlCc]\s*\.?\s*\d{1,4}\s*/\s*\d{1,4}")
_EU_DATE_RE = re.compile(r"\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{2,4}")
_DOC_TYPE_RE = re.compile(
    r"(?P<tipo>"
    r"REGOLAMENTO\s+\((?:UE|CE|CEE)\)\s+N\.\s*\d+/\d+|"
    r"DIRETTIVA\s+\d+/\d+/(?:UE|CE|CEE)|"
    r"DECISIONE\s+\d+/\d+/(?:UE|CE|PESC)|"
    r"REGOLAMENTO\s+DELEGATO\s+\(UE\)\s+\d+/\d+|"
    r"REGOLAMENTO\s+DI\s+ESECUZIONE\s+\(UE\)\s+\d+/\d+"
    r")",
    re.IGNORECASE,
)
_ARTICOLO_RE = re.compile(r"^\s*Articolo\s+(?P<num>\d+)\s*$", re.IGNORECASE)
_CAPITOLO_RE = re.compile(r"^\s*CAPITOLO\s+(?P<num>[IVXLC]+|\d+)\s*$", re.IGNORECASE)
_TITOLO_RE = re.compile(r"^\s*TITOLO\s+(?P<num>[IVXLC]+|\d+)\s*$", re.IGNORECASE)
_SEZIONE_RE = re.compile(r"^\s*SEZIONE\s+(?P<num>[IVXLC]+|\d+)\s*$", re.IGNORECASE)
_ALLEGATO_RE = re.compile(r"^\s*ALLEGATO\s*(?P<num>[IVXLCivxlc]+|\d+|[A-Za-z]+)?\s*$", re.IGNORECASE)
_CONSIDERANDO_RE = re.compile(r"^\s*\((?P<num>\d+)\)\s+")
_ADOTTATO_RE = re.compile(r"(?:HA|HANNO)\s+ADOTTATO\s+(?:IL\s+PRESENTE|LA\s+PRESENTE)", re.IGNORECASE)
_PAGE_NUM_RE = re.compile(r"^\s*\d{1,5}\s*$")
_FOOTNOTE_RE = re.compile(r"^\s*\(\s*[¹²³⁴⁵⁶⁷⁸⁹\d]+\s*\)")


def identify_repeated_lines_gazzetta(doc, min_repeats=2):
    line_counts = Counter()
    for page in doc:
        blocks = page.get_text("blocks")
        height = page.rect.height
        for block in blocks:
            y0 = block[1]
            text = block[4].strip() if len(block) > 4 else ""
            if not text or len(text) < 3:
                continue
            if y0 < height * 0.08 or y0 > height * 0.92:
                line_counts[text] += 1
    return {line for line, count in line_counts.items() if count >= min_repeats}


def _is_header_footer(text, repeated_lines):
    stripped = text.strip()
    if not stripped:
        return True
    if stripped in repeated_lines:
        return True
    if _PAGE_NUM_RE.match(stripped):
        return True
    if _GAZZETTA_HEADER_RE.search(stripped):
        return True
    if _EU_DATE_RE.search(stripped) and _PAGE_REF_RE.search(stripped):
        return True
    if _EU_DATE_RE.match(stripped) and len(stripped) < 20:
        return True
    if _PAGE_REF_RE.match(stripped) and len(stripped) < 15:
        return True
    if stripped == "IT":
        return True
    return False


def _extract_document_metadata(doc):
    first_page_text = doc[0].get_text() if doc.page_count > 0 else ""
    metadata = {
        "tipo_documento": "",
        "numero_documento": "",
        "data_documento": "",
        "istituzione": "",
        "oggetto": "",
    }
    type_match = _DOC_TYPE_RE.search(first_page_text)
    if type_match:
        metadata["tipo_documento"] = type_match.group("tipo").strip()
    institutions = [
        "CONSIGLIO DELL'UNIONE EUROPEA",
        "PARLAMENTO EUROPEO E DEL CONSIGLIO",
        "COMMISSIONE EUROPEA",
        "CONSIGLIO",
        "PARLAMENTO EUROPEO",
        "BANCA CENTRALE EUROPEA",
    ]
    upper_text = first_page_text.upper()
    for inst in institutions:
        if inst in upper_text:
            metadata["istituzione"] = inst
            break
    date_match = re.search(r"del\s+(\d{1,2}\s+\w+\s+\d{4})", first_page_text, re.IGNORECASE)
    if date_match:
        metadata["data_documento"] = date_match.group(1).strip()
    return metadata


def parser_gazzetta_ue(pdf_path):
    doc = fitz.open(pdf_path)
    repeated_lines = identify_repeated_lines_gazzetta(doc, min_repeats=2)
    debug_log = []
    metadata = _extract_document_metadata(doc)
    all_lines = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        for line in text.split("\n"):
            stripped = line.strip()
            if _is_header_footer(stripped, repeated_lines):
                continue
            if _FOOTNOTE_RE.match(stripped):
                continue
            if stripped:
                all_lines.append((page_num, stripped))
    doc.close()

    result = []
    current_chapter = ""
    current_section = ""
    current_article = None
    current_allegato = None
    preamble_text = []
    in_preamble = True
    considerandi = []

    for i, (page_num, line) in enumerate(all_lines):
        if in_preamble and _ADOTTATO_RE.search(line):
            in_preamble = False
            if preamble_text or considerandi:
                preamble_content = "\n".join(preamble_text)
                considerandi_content = "\n".join(f"({c['num']}) {c['text']}" for c in considerandi)
                full_preamble = preamble_content
                if considerandi_content:
                    full_preamble += "\n\nconsiderando quanto segue:\n" + considerandi_content
                result.append({
                    "codicedocumento": metadata["tipo_documento"],
                    "page": 0,
                    "identificativo": "PREAMBOLO",
                    "titolo": "Preambolo",
                    "codicearticolo": "",
                    "contenuto": full_preamble.strip(),
                    "contenuto_parsato": [{
                        "tipo": "testo",
                        "identificativo": "PREAMBOLO",
                        "titolo_articolo": "Preambolo e considerando",
                        "contenuto": full_preamble.strip(),
                    }],
                })
            continue

        if in_preamble:
            cons_match = _CONSIDERANDO_RE.match(line)
            if cons_match:
                num = cons_match.group("num")
                text_after = line[cons_match.end():].strip()
                considerandi.append({"num": num, "text": text_after})
            elif considerandi:
                considerandi[-1]["text"] += " " + line
            else:
                preamble_text.append(line)
            continue

        cap_match = _CAPITOLO_RE.match(line)
        if cap_match:
            current_chapter = f"CAPITOLO {cap_match.group('num')}"
            if i + 1 < len(all_lines):
                _, next_line = all_lines[i + 1]
                if not _ARTICOLO_RE.match(next_line) and not _ALLEGATO_RE.match(next_line):
                    current_chapter += f" - {next_line}"
            continue

        if _TITOLO_RE.match(line):
            continue
        sez_match = _SEZIONE_RE.match(line)
        if sez_match:
            current_section = f"SEZIONE {sez_match.group('num')}"
            continue

        all_match = _ALLEGATO_RE.match(line)
        if all_match:
            if current_article:
                _finalize_article(current_article, result, metadata, debug_log)
                current_article = None
            if current_allegato:
                _finalize_allegato(current_allegato, result, metadata, debug_log)
            num = all_match.group("num") or ""
            allegato_id = f"ALLEGATO {num}".strip()
            allegato_title = ""
            if i + 1 < len(all_lines):
                _, next_line = all_lines[i + 1]
                if not _ARTICOLO_RE.match(next_line) and not _ALLEGATO_RE.match(next_line):
                    allegato_title = next_line
            current_allegato = {
                "id": allegato_id,
                "title": allegato_title,
                "page": page_num,
                "text_parts": [],
                "skip_next": bool(allegato_title),
            }
            continue

        if current_allegato:
            if current_allegato.get("skip_next"):
                current_allegato["skip_next"] = False
                continue
            current_allegato["text_parts"].append(line)
            continue

        art_match = _ARTICOLO_RE.match(line)
        if art_match:
            if current_article:
                _finalize_article(current_article, result, metadata, debug_log)
            art_num = art_match.group("num")
            art_title = ""
            if i + 1 < len(all_lines):
                _, next_line = all_lines[i + 1]
                if (not _ARTICOLO_RE.match(next_line) and not _ALLEGATO_RE.match(next_line) and len(next_line) < 120):
                    art_title = next_line
            current_article = {
                "num": art_num,
                "title": art_title,
                "chapter": current_chapter,
                "section": current_section,
                "page": page_num,
                "text_parts": [],
                "skip_next": bool(art_title),
            }
            continue

        if current_article:
            if current_article.get("skip_next"):
                current_article["skip_next"] = False
                continue
            current_article["text_parts"].append(line)

    if current_article:
        _finalize_article(current_article, result, metadata, debug_log)
    if current_allegato:
        _finalize_allegato(current_allegato, result, metadata, debug_log)

    log_dir = Path(_SRCDIR / "out_parser")
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(os.path.join(log_dir, "debug_log_parseGazzettaUE.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(debug_log))
    with open(os.path.join(log_dir, "result_GazzettaUE.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def _finalize_article(article, result, metadata, debug_log):
    content = "\n".join(article["text_parts"]).strip()
    content_clean = content.replace("\n", " ").replace("- ", "-").strip()
    titolo = f"Articolo {article['num']}"
    if article["title"]:
        titolo += f" - {article['title']}"
    if article["chapter"]:
        titolo = f"{article['chapter']} > {titolo}"
    result.append({
        "codicedocumento": metadata["tipo_documento"],
        "page": article["page"],
        "identificativo": f"Art. {article['num']}",
        "titolo": titolo,
        "codicearticolo": "",
        "contenuto": content_clean,
        "contenuto_parsato": [{
            "tipo": "testo",
            "identificativo": f"Art. {article['num']}",
            "titolo_articolo": titolo,
            "contenuto": content_clean,
        }],
    })
    debug_log.append(f"FINALIZED Art. {article['num']}")


def _finalize_allegato(allegato, result, metadata, debug_log):
    content = "\n".join(allegato["text_parts"]).strip()
    content_clean = content.replace("\n", " ").replace("- ", "-").strip()
    titolo = allegato["id"]
    if allegato["title"]:
        titolo += f" - {allegato['title']}"
    result.append({
        "codicedocumento": metadata["tipo_documento"],
        "page": allegato["page"],
        "identificativo": allegato["id"],
        "titolo": titolo,
        "codicearticolo": "",
        "contenuto": content_clean,
        "contenuto_parsato": [{
            "tipo": "testo",
            "identificativo": allegato["id"],
            "titolo_articolo": titolo,
            "contenuto": content_clean,
        }],
    })
    debug_log.append(f"FINALIZED {allegato['id']}")


def looks_like_gazzetta_ue_document(pdf_path, pdf_name=""):
    name = (pdf_name or "").lower()
    if any(token in name for token in ("celex", "gazzetta", "gu_", "oj_")):
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
    if _GAZZETTA_HEADER_RE.search(head_text):
        score += 3
    if _DOC_TYPE_RE.search(head_text):
        score += 2
    if re.search(r"\bArticolo\s+\d+\b", head_text):
        score += 1
    if re.search(r"(?:CONSIGLIO|PARLAMENTO\s+EUROPEO|COMMISSIONE)", head_text, re.IGNORECASE):
        score += 1
    if _PAGE_REF_RE.search(head_text):
        score += 1
    if re.search(r"\bconsiderando\b", head_text, re.IGNORECASE):
        score += 1
    return score >= 4
