"""
Parser for tabular annex documents.
Handles structured table-based annexes
"""

import re
import fitz  # PyMuPDF
import json
import os
from collections import OrderedDict
from pathlib import Path


_SRCDIR = Path(__file__).resolve().parents[2]
_MARGIN_RE = re.compile(
    r"Official\s+Journal|Gazzetta\s+ufficiale|"
    r"L\s+\d{1,4}\s*/\s*\d{1,4}|"
    r"\d{1,2}\.\d{1,2}\.\d{4}",
    re.IGNORECASE,
)
_SECTION_NUM_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2})?$")
_SUB_ITEM_RE = re.compile(r"^[a-z]$")
_DATA_FORMAT_RE = re.compile(
    r"^(?:an?\.\.\d+|an?\d+|n\.\.\d+|n\d+|date(?:Time)?|\d+x)$",
    re.IGNORECASE,
)
_COL_LETTERS = {"A", "B", "C", "D", "E", "F", "G"}


def looks_like_annex_tabular_document(pdf_path, pdf_name=""):
    name = (pdf_name or "").lower()
    if any(token in name for token in ("emcs", "annex_tab", "allegato_tab")):
        return True
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return False
    try:
        scan_pages = min(15, doc.page_count)
        score = 0
        head_text = "\n".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
        if re.search(r"Official\s+Journal|Gazzetta\s+ufficiale", head_text, re.IGNORECASE):
            score += 2
        if re.search(r"L\s+\d{1,4}\s*/\s*\d{1,4}", head_text):
            score += 1
        pages_with_ag_tables = 0
        for i in range(scan_pages):
            tabs = doc[i].find_tables()
            if tabs.tables:
                for table in tabs.tables:
                    data = table.extract()
                    if data and _is_header_row(data[0]):
                        pages_with_ag_tables += 1
                        break
        if pages_with_ag_tables >= 3:
            score += 3
        elif pages_with_ag_tables >= 1:
            score += 2
        sample_pages = list(range(scan_pages))
        if doc.page_count > 15:
            mid = doc.page_count // 2
            sample_pages += [mid - 1, mid, mid + 1]
        total_table_pages = 0
        for i in set(sample_pages):
            if i < doc.page_count:
                tabs = doc[i].find_tables()
                if tabs.tables:
                    total_table_pages += 1
        if len(sample_pages) > 0 and total_table_pages / len(set(sample_pages)) > 0.6:
            score += 1
        broader_text = "\n".join(
            doc[i].get_text() for i in range(min(scan_pages, doc.page_count))
        )
        if re.search(r"\ban?\.\.\d+\b", broader_text):
            score += 1
        if re.search(r"\bANNEX\b|\bALLEGATO\b", head_text, re.IGNORECASE):
            score += 1
        return score >= 5
    finally:
        doc.close()


def _is_header_row(row):
    if not row:
        return False
    non_none = [c.strip() if c else "" for c in row]
    non_empty = [c for c in non_none if c]
    if not non_empty:
        return False
    letters = {c.upper() for c in non_empty if len(c) == 1 and c.upper() in _COL_LETTERS}
    return len(letters) >= 4


def _is_empty_row(row):
    return all(not c or not c.strip() for c in row)


def _is_margin_row(row):
    text = " ".join(c or "" for c in row).strip()
    if not text:
        return True
    if len(text) < 5:
        return True
    if _MARGIN_RE.search(text) and len(text) < 100:
        non_margin = _MARGIN_RE.sub("", text).strip()
        if len(non_margin) < 10:
            return True
    if text.strip() in ("EN", "IT", "FR", "DE", "ES", "PT", "NL"):
        return True
    return False


def _detect_column_mapping(table_data):
    for row in table_data[:3]:
        if _is_header_row(row):
            mapping = {}
            for i, cell in enumerate(row):
                if cell and cell.strip().upper() in _COL_LETTERS and len(cell.strip()) == 1:
                    mapping[cell.strip().upper()] = i
            if len(mapping) >= 4:
                return mapping
    return None


def _parse_row(row, col_mapping):
    def get_cell(letter):
        idx = col_mapping.get(letter)
        if idx is not None and idx < len(row):
            val = row[idx]
            return val.strip() if val else ""
        return ""

    f_idx = col_mapping.get("F")
    g_idx = col_mapping.get("G")
    description = ""
    if f_idx is not None and g_idx is not None and g_idx > f_idx + 1:
        parts = []
        for i in range(f_idx, g_idx):
            if i < len(row) and row[i]:
                parts.append(row[i].strip())
        description = " | ".join(parts) if parts else ""
    else:
        description = get_cell("F")

    return {
        "section_num": get_cell("A"),
        "sub_item": get_cell("B"),
        "element_name": get_cell("C"),
        "requirement": get_cell("D"),
        "condition": get_cell("E"),
        "description": description,
        "data_format": get_cell("G"),
    }


def _merge_continuation_rows(all_rows):
    merged = []
    current = None
    for page_num, row_data in all_rows:
        col_a = row_data.get("section_num", "").strip()
        col_b = row_data.get("sub_item", "").strip()
        col_c = row_data.get("element_name", "").strip()
        col_d = row_data.get("requirement", "").strip()
        has_identifier = bool(col_a or col_b or (col_c and col_d))
        if has_identifier:
            if current:
                merged.append(current)
            current = {"page": page_num, **row_data}
        elif current:
            for key in ("element_name", "condition", "description", "data_format"):
                if row_data.get(key):
                    existing = current.get(key, "") or ""
                    sep = "\n" if key in ("condition", "description") else " "
                    current[key] = (existing + sep + row_data[key]).strip()
    if current:
        merged.append(current)
    return merged


def _build_output(merged_rows, doc_code, debug_log):
    sections = OrderedDict()
    current_section = None
    current_section_title = ""
    for row in merged_rows:
        sec_num = row.get("section_num", "").strip()
        sub_item = row.get("sub_item", "").strip()
        element_name = row.get("element_name", "").strip()
        if sec_num and _SECTION_NUM_RE.match(sec_num):
            if not sub_item:
                current_section = sec_num
                current_section_title = element_name.replace("\n", " ")
                if current_section not in sections:
                    sections[current_section] = {
                        "title": current_section_title,
                        "page": row.get("page", 0),
                        "requirement": row.get("requirement", ""),
                        "condition": row.get("condition", ""),
                        "description": row.get("description", ""),
                        "data_format": row.get("data_format", ""),
                        "sub_items": [],
                    }
                debug_log.append(f"  [SECTION] {sec_num} - {element_name}")
            else:
                if sec_num not in sections:
                    current_section = sec_num
                    current_section_title = ""
                    sections[current_section] = {
                        "title": "",
                        "page": row.get("page", 0),
                        "requirement": "",
                        "condition": "",
                        "description": "",
                        "data_format": "",
                        "sub_items": [],
                    }
                sections[sec_num]["sub_items"].append(row)
                debug_log.append(f"    [SUB-ITEM] {sec_num}.{sub_item} - {element_name}")
        elif sec_num and re.match(r"^\d{1,2}$", sec_num):
            current_section = sec_num
            current_section_title = element_name.replace("\n", " ")
            if current_section not in sections:
                sections[current_section] = {
                    "title": current_section_title,
                    "page": row.get("page", 0),
                    "requirement": row.get("requirement", ""),
                    "condition": row.get("condition", ""),
                    "description": row.get("description", ""),
                    "data_format": row.get("data_format", ""),
                    "sub_items": [],
                }
            debug_log.append(f"  [SECTION] {sec_num} - {current_section_title}")
        elif sub_item and current_section:
            sections[current_section]["sub_items"].append(row)
            debug_log.append(f"    [SUB-ITEM] {current_section}.{sub_item} - {element_name}")
        elif current_section and element_name:
            sections[current_section]["sub_items"].append(row)

    result = []
    for section_num, section_data in sections.items():
        section_title = section_data["title"]
        titolo = f"{section_num} - {section_title}" if section_title else section_num
        contenuto_parsato = []
        for sub_row in section_data["sub_items"]:
            sub_item = sub_row.get("sub_item", "").strip()
            element_name = sub_row.get("element_name", "").strip().replace("\n", " ")
            requirement = sub_row.get("requirement", "").strip()
            condition = sub_row.get("condition", "").strip()
            description = sub_row.get("description", "").strip()
            data_format = sub_row.get("data_format", "").strip()
            sub_id = f"{section_num}.{sub_item}" if sub_item else section_num
            content_parts = []
            if element_name:
                content_parts.append(f"Data Element: {element_name}")
            if requirement:
                content_parts.append(f"Requirement: {requirement}")
            if condition:
                content_parts.append(f"Condition: {condition}")
            if description:
                content_parts.append(f"Description: {description}")
            if data_format:
                content_parts.append(f"Format: {data_format}")
            content_str = " | ".join(content_parts)
            contenuto_parsato.append({
                "identificativo": sub_id,
                "titolo_articolo": titolo,
                "contenuto": content_str,
                "contenuto_parsato_2": [{
                    "identificativo": sub_id,
                    "contenuto": content_str,
                    "flag": True,
                }],
            })

        if not contenuto_parsato:
            sec_content_parts = []
            if section_title:
                sec_content_parts.append(f"Data Element: {section_title}")
            if section_data.get("requirement"):
                sec_content_parts.append(f"Requirement: {section_data['requirement']}")
            if section_data.get("condition"):
                sec_content_parts.append(f"Condition: {section_data['condition']}")
            if section_data.get("description"):
                sec_content_parts.append(f"Description: {section_data['description']}")
            if section_data.get("data_format"):
                sec_content_parts.append(f"Format: {section_data['data_format']}")
            sec_content = " | ".join(sec_content_parts) if sec_content_parts else titolo
            contenuto_parsato.append({
                "identificativo": section_num,
                "titolo_articolo": titolo,
                "contenuto": sec_content,
                "contenuto_parsato_2": [{
                    "identificativo": section_num,
                    "contenuto": sec_content,
                    "flag": True,
                }],
            })

        full_content = "\n".join(cp["contenuto"] for cp in contenuto_parsato)
        result.append({
            "codicedocumento": doc_code,
            "page": section_data["page"],
            "identificativo": section_num,
            "titolo": titolo,
            "codicearticolo": "",
            "contenuto": full_content,
            "contenuto_parsato": contenuto_parsato,
        })
    return result


def _extract_doc_code(doc):
    if doc.page_count == 0:
        return ""
    first_text = doc[0].get_text()
    match = re.search(r"\b(\d{4}/\d{3,4})\b", first_text)
    if match:
        return match.group(1)
    match = re.search(r"L\s+(\d{1,4})", first_text)
    if match:
        return f"L {match.group(1)}"
    return ""


def parser_annex_tabular(pdf_path):
    doc = fitz.open(pdf_path)
    print(f"############################## PARSER ANNEX TABULAR ###############################")
    print(f"[INFO] Documento PDF caricato: {pdf_path}")
    print(f"[INFO] Pagine: {len(doc)}")
    debug_log = []
    debug_log.append("=" * 80)
    debug_log.append("PARSER ANNEX TABULAR - Inizio analisi")
    debug_log.append(f"File: {pdf_path}")
    debug_log.append(f"Pagine: {len(doc)}")
    debug_log.append("=" * 80)
    doc_code = _extract_doc_code(doc)
    debug_log.append(f"Document code: {doc_code}")
    all_rows = []
    found_any_mapping = False

    for page_num in range(len(doc)):
        page = doc[page_num]
        tabs = page.find_tables()
        page_rows = 0
        skipped = 0
        if not tabs.tables:
            debug_log.append(f"\n--- Pagina {page_num + 1} --- Nessuna tabella trovata")
            continue
        for table in tabs.tables:
            data = table.extract()
            if not data:
                continue
            page_col_mapping = _detect_column_mapping(data)
            if page_col_mapping is None:
                continue
            if not found_any_mapping:
                debug_log.append(f"First column mapping detected on page {page_num + 1}: {page_col_mapping}")
                found_any_mapping = True
            for row in data:
                if _is_header_row(row):
                    skipped += 1
                    continue
                if _is_empty_row(row):
                    skipped += 1
                    continue
                if _is_margin_row(row):
                    skipped += 1
                    continue
                parsed = _parse_row(row, page_col_mapping)
                all_rows.append((page_num, parsed))
                page_rows += 1
        debug_log.append(
            f"\n--- Pagina {page_num + 1} --- "
            f"Tabelle: {len(tabs.tables)}, Righe estratte: {page_rows}, Filtrate: {skipped}"
        )
    doc.close()

    if not found_any_mapping:
        print("[WARN] Annex Tabular: nessun mapping colonne trovato, fallback vuoto")
        debug_log.append("[WARN] No column mapping found — returning empty result")
        _save_debug(debug_log, [])
        return []

    debug_log.append(f"\nTotale righe grezze estratte: {len(all_rows)}")
    merged = _merge_continuation_rows(all_rows)
    debug_log.append(f"Righe dopo merge continuazioni: {len(merged)}")
    result = _build_output(merged, doc_code, debug_log)
    debug_log.append(f"\nSezioni finali: {len(result)}")
    _save_debug(debug_log, result)
    print(f"[INFO] Annex Tabular parsing completato: {len(result)} sezioni trovate")
    return result


def _save_debug(debug_log, result):
    log_dir = Path(_SRCDIR / "out_parser")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = os.path.join(log_dir, "debug_log_parseAnnexTabular.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(debug_log))
    result_path = os.path.join(log_dir, "result_AnnexTabular.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
