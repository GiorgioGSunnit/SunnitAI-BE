"""
Generic parser for documents with no standard legal structure.

Four-level cascade — each level is tried in order; the first to yield ≥ 3
records is accepted:

  Level 1 – title-block splitting via extract_page_blocks()
  Level 2 – numbered-heading splitting via extract_page_text()
  Level 3 – double-newline paragraph splitting
  Level 4 – fixed-size 500-word chunks with 50-word overlap

All records have the flat format expected by build_neo4j_graph_payload.
"""
from __future__ import annotations

import re

import fitz  # PyMuPDF

from lex_package.utils.pdf_extract import extract_page_blocks, extract_page_text

_NUMBERED_HEADING_RE = re.compile(
    r"""^\s*(?:
        (?:Article|Articolo)\s+\d+      |   # Article 1, Articolo 1
        [IVXLCDM]+\.(?:\s|$)            |   # I., II., …
        \d+(?:\.\d+)+(?:\s|$)           |   # 1.1, 1.1.1
        \d+\.(?:\s|$)                       # 1.
    )""",
    re.VERBOSE | re.IGNORECASE,
)

_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?](?:\s|$)")


def _make_abstract(text: str) -> str:
    words = text.split()
    if not words:
        return ""
    if len(words) <= 200:
        return text.strip()
    candidate = " ".join(words[:200])
    matches = list(_SENTENCE_BOUNDARY_RE.finditer(candidate))
    if matches:
        cut = matches[-1].start() + 1
        return candidate[:cut].strip()
    return candidate.strip()


def _make_record(counter: int | str, title: str, content: str, page: int) -> dict:
    articolo_id = f"{counter:03d}" if isinstance(counter, int) else str(counter)
    return {
        "Tipo": "Articolo",
        "Articolo": articolo_id,
        "Titolo Articolo": title,
        "Contenuto": content,
        "Pagina": page,
        "Hash": "",
        "abstract": _make_abstract(content),
    }


def _split_oversized(records: list[dict], max_words: int = 800) -> list[dict]:
    """Post-process records, splitting any exceeding max_words into lettered overflow chunks.

    "005" → first chunk keeps "005"; overflow chunks get "005a", "005b", …
    Applied as a safety net to all level outputs.
    """
    result: list[dict] = []
    for rec in records:
        words = rec["Contenuto"].split()
        if len(words) <= max_words:
            result.append(rec)
            continue
        base_id = rec["Articolo"]
        title = rec["Titolo Articolo"]
        page = rec["Pagina"]
        overflow_idx = 0
        i = 0
        while i < len(words):
            chunk_text = " ".join(words[i : i + max_words])
            if i == 0:
                art_id = base_id
            else:
                art_id = f"{base_id}{chr(ord('a') + overflow_idx)}"
                overflow_idx += 1
            result.append(_make_record(art_id, title, chunk_text, page))
            i += max_words
    return result


def _level1_title_blocks(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    records: list[dict] = []
    counter = 0
    section_title = ""
    section_content: list[str] = []
    section_page = 1
    section_word_count = 0
    section_emitted = False      # True once the first chunk of this section is out
    section_overflow_count = 0   # counts "a", "b", … suffixes used so far

    def _flush() -> None:
        nonlocal counter, section_content, section_word_count
        nonlocal section_emitted, section_overflow_count
        content = "\n".join(section_content).strip()
        if not content:
            return
        if not section_emitted:
            counter += 1
            art_id = f"{counter:03d}"
            section_emitted = True
        else:
            art_id = f"{counter:03d}{chr(ord('a') + section_overflow_count)}"
            section_overflow_count += 1
        records.append(_make_record(art_id, section_title, content, section_page))
        section_content = []
        section_word_count = 0

    for page_num, page in enumerate(doc, start=1):
        for block in extract_page_blocks(page):
            text = block.get("text", "").strip()
            if not text:
                continue
            if block.get("type") == "title":
                _flush()
                section_title = text
                section_page = page_num
                section_emitted = False
                section_overflow_count = 0
            else:
                section_content.append(text)
                section_word_count += len(text.split())
                if section_word_count > 800:
                    _flush()
                    section_page = page_num  # next overflow chunk starts here

    _flush()
    doc.close()
    return records


def _level2_numbered_headings(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    records: list[dict] = []
    counter = 0
    section_title = ""
    section_lines: list[str] = []
    section_page = 1
    section_word_count = 0
    section_emitted = False
    section_overflow_count = 0

    def _flush() -> None:
        nonlocal counter, section_lines, section_word_count
        nonlocal section_emitted, section_overflow_count
        content = "\n".join(section_lines).strip()
        if not content:
            return
        if not section_emitted:
            counter += 1
            art_id = f"{counter:03d}"
            section_emitted = True
        else:
            art_id = f"{counter:03d}{chr(ord('a') + section_overflow_count)}"
            section_overflow_count += 1
        records.append(_make_record(art_id, section_title, content, section_page))
        section_lines = []
        section_word_count = 0

    for page_num, page in enumerate(doc, start=1):
        for line in extract_page_text(page).splitlines():
            if _NUMBERED_HEADING_RE.match(line):
                _flush()
                section_title = line.strip()
                section_page = page_num
                section_emitted = False
                section_overflow_count = 0
            else:
                section_lines.append(line)
                section_word_count += len(line.split())
                if section_word_count > 800:
                    _flush()
                    section_page = page_num  # next overflow chunk starts here

    _flush()
    doc.close()
    return records


def _level3_paragraph_split(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    records: list[dict] = []
    counter = 0
    buf_lines: list[str] = []
    buf_page = 1
    saw_blank = False

    for page_num, page in enumerate(doc, start=1):
        for line in extract_page_text(page).splitlines():
            stripped = line.strip()
            if not stripped:
                saw_blank = True
            else:
                if saw_blank and buf_lines:
                    content = "\n".join(buf_lines).strip()
                    if len(content) > 100:
                        first = buf_lines[0].strip()
                        title = first if len(first) < 80 else ""
                        counter += 1
                        records.append(_make_record(counter, title, content, buf_page))
                    buf_lines = []
                    buf_page = page_num
                if not buf_lines:
                    buf_page = page_num
                buf_lines.append(stripped)
                saw_blank = False

    if buf_lines:
        content = "\n".join(buf_lines).strip()
        if len(content) > 100:
            first = buf_lines[0].strip()
            title = first if len(first) < 80 else ""
            counter += 1
            records.append(_make_record(counter, title, content, buf_page))

    doc.close()
    return records


def _level4_fixed_chunks(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    word_pages: list[tuple[str, int]] = []
    for page_num, page in enumerate(doc, start=1):
        for word in extract_page_text(page).split():
            word_pages.append((word, page_num))
    doc.close()

    chunk_size = 500
    overlap = 50
    stride = chunk_size - overlap
    records: list[dict] = []
    counter = 0
    i = 0

    while i < len(word_pages):
        chunk = word_pages[i : i + chunk_size]
        text = " ".join(w for w, _ in chunk)
        if text.strip():
            counter += 1
            records.append(_make_record(counter, "", text, chunk[0][1]))
        i += stride

    return records


def parser_generic(pdf_path: str) -> list[dict]:
    """
    Parse a document with no standard legal structure.

    Tries four strategies in order and returns the first that yields ≥ 3 records.
    If none reach 3 records the result of the last level is returned.
    _split_oversized is applied to every level's output as a safety net.
    """
    result: list[dict] = []
    for level_fn in (
        _level1_title_blocks,
        _level2_numbered_headings,
        _level3_paragraph_split,
        _level4_fixed_chunks,
    ):
        try:
            records = _split_oversized(level_fn(pdf_path))
        except Exception:
            records = []
        result = records
        if len(records) >= 3:
            return records

    return result
