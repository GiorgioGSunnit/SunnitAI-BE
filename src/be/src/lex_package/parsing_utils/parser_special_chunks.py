"""
Special-chunk parser — a structure-agnostic fallback for documents flagged
with the "[SPECIAL]" name prefix.

Splits the raw extracted text into paragraph-sized chunks using blank lines,
numbered/lettered list markers ("1.", "2)", "a)", "b.") and bullet characters
(•, -, *, –, ▪) as boundaries, then normalises chunk size (grouping short
fragments, capping long ones) before emitting them as plain "articolo" dicts
compatible with the rest of the parsing pipeline.
"""
from __future__ import annotations

import re

import fitz  # PyMuPDF

_NUMBERED_RE = re.compile(r"^\s*(?:\d+|[a-zA-Z])[.)]\s")
_BULLET_RE = re.compile(r"^\s*[•\-*–▪]\s")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=\.)\s+(?=[A-Z])")

_MIN_WORDS = 10
_GROUP_THRESHOLD_WORDS = 50
_MAX_WORDS = 800

_ANNOTATION_CODE_RE = re.compile(r'\([!\?\+\*\®©\d°\'’]+\)\s*')
_CROSS_REF_RE = re.compile(r'Vai al commento\b[^\n]*', flags=re.IGNORECASE)
_ARROW_SYMBOL_RE = re.compile(r'[Î→↓▼➜➡⬇←-⇿]')
_EXTRA_NEWLINES_RE = re.compile(r'\n{3,}')


def _clean_special_text(text: str) -> str:
    """Strip publisher annotation codes, cross-reference labels, and stray
    arrow/symbol glyphs from raw extracted text before chunking."""
    text = _ANNOTATION_CODE_RE.sub('', text)
    text = _CROSS_REF_RE.sub('', text)
    text = _ARROW_SYMBOL_RE.sub('', text)
    text = _EXTRA_NEWLINES_RE.sub('\n\n', text)
    text = re.sub(r'©[^\n]{0,80}', '', text)
    text = re.sub(r'Copyright\s*©?\s*\d{0,4}\s*[\w\s]*', '', text, flags=re.IGNORECASE)
    return text.strip()


def _split_page_into_raw_chunks(text: str) -> list[str]:
    """Split one page's text into raw chunks at blank-line / list-marker boundaries."""
    if not text or not text.strip():
        return []

    raw_chunks: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        paragraph = paragraph.strip("\n")
        if not paragraph.strip():
            continue

        current: list[str] = []
        for line in paragraph.split("\n"):
            starts_new_item = bool(_NUMBERED_RE.match(line) or _BULLET_RE.match(line))
            if starts_new_item and current:
                raw_chunks.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)
        if current:
            joined = "\n".join(current).strip()
            if joined:
                raw_chunks.append(joined)

    return raw_chunks


def _extract_raw_chunks(pdf_path: str) -> list[tuple[str, int]]:
    """Return (chunk_text, page_number) pairs, page-by-page, page numbers 0-based."""
    doc = fitz.open(pdf_path)
    raw: list[tuple[str, int]] = []
    try:
        for page_index, page in enumerate(doc):
            try:
                page_text = page.get_text()
                page_text = _clean_special_text(page_text)
            except Exception:
                page_text = ""
            for chunk_text in _split_page_into_raw_chunks(page_text):
                raw.append((chunk_text, page_index))
    finally:
        doc.close()
    return raw


def _group_short_chunks(raw: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Merge runs of consecutive chunks under 50 words into the next chunk."""
    grouped: list[tuple[str, int]] = []
    buffer_text = ""
    buffer_page: int | None = None

    for text, page in raw:
        if buffer_text:
            buffer_text = buffer_text + "\n" + text
        else:
            buffer_text = text
            buffer_page = page

        if len(buffer_text.split()) < _GROUP_THRESHOLD_WORDS:
            continue

        grouped.append((buffer_text, buffer_page))
        buffer_text = ""
        buffer_page = None

    if buffer_text:
        grouped.append((buffer_text, buffer_page))

    return grouped


def _split_at_word_cap(text: str, page: int) -> list[tuple[str, int]]:
    """Split text at the nearest sentence boundary whenever it exceeds 800 words."""
    if len(text.split()) <= _MAX_WORDS:
        return [(text, page)]

    result: list[tuple[str, int]] = []
    remaining = text

    while len(remaining.split()) > _MAX_WORDS:
        target_words = remaining.split()[:_MAX_WORDS]
        target_char_len = len(" ".join(target_words))

        boundaries = [m.start() for m in _SENTENCE_BOUNDARY_RE.finditer(remaining)]
        cut = min(boundaries, key=lambda pos: abs(pos - target_char_len)) if boundaries else 0
        if cut <= 0:
            cut = target_char_len

        piece = remaining[:cut].strip()
        if not piece:
            cut = target_char_len
            piece = remaining[:cut].strip()

        result.append((piece, page))
        remaining = remaining[cut:].strip()

    if remaining:
        result.append((remaining, page))

    return result


def parser_special_chunks(pdf_path: str) -> list[dict]:
    """
    Extract text from *pdf_path* and split it into normalised chunks.

    Returns a list of "articolo" dicts:
        {
            "titolo": first 80 chars of the chunk (newlines stripped),
            "contenuto": full chunk text,
            "contenuto_parsato": [{"testo": full chunk text, "tipo": "comma"}],
            "page": 0-based page number where the chunk starts,
            "tipo": "special_chunk",
        }
    """
    raw_chunks = _extract_raw_chunks(pdf_path)
    grouped = _group_short_chunks(raw_chunks)

    capped: list[tuple[str, int]] = []
    for text, page in grouped:
        capped.extend(_split_at_word_cap(text, page))

    articoli: list[dict] = []
    for text, page in capped:
        if len(text.split()) < _MIN_WORDS:
            continue

        title = text.replace("\n", " ")[:80]
        articoli.append({
            "titolo": title,
            "contenuto": text,
            "contenuto_parsato": [{"contenuto": text, "tipo": "comma"}],
            "page": page,
            "tipo": "special_chunk",
        })

    return articoli
