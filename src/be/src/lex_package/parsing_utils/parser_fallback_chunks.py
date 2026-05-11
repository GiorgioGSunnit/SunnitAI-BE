"""
Generic sliding-window chunk fallback parser.

Used as the absolute last resort when all structural parsers return empty.
Splits the document's extracted text into fixed-size overlapping chunks of
approximately chunk_size words, regardless of document structure.

This guarantees that text-based PDFs always produce at least some sections,
even if no structural parser recognises the document format (e.g. court
decisions, international conventions, regulatory circulars).

Scanned PDFs with no extractable text will still produce 0 chunks — acceptable.
"""
from __future__ import annotations

import re

_SENTENCE_END_RE = re.compile(r"(?<=[.?!])\s+")


def parser_fallback_chunks(
    text: str,
    chunk_size: int = 600,
    overlap: int = 50,
) -> list[dict]:
    """
    Split *text* into overlapping word-based chunks and return them as articoli.

    Args:
        text:       Full extracted text of the document.
        chunk_size: Target chunk size in words.
        overlap:    Number of words to carry over into the next chunk.

    Returns:
        List of articoli dicts compatible with the rest of the parsing pipeline.
    """
    if not text or not text.strip():
        return []

    # Split into sentences first, then group into chunks
    sentences = [s.strip() for s in _SENTENCE_END_RE.split(text) if s.strip()]

    chunks: list[str] = []
    current_words: list[str] = []

    for sentence in sentences:
        sentence_words = sentence.split()
        current_words.extend(sentence_words)

        if len(current_words) >= chunk_size:
            chunk_text = " ".join(current_words)
            chunks.append(chunk_text)
            # Keep last `overlap` words for the next chunk
            current_words = current_words[-overlap:]

    # Flush remaining words as final chunk
    if current_words:
        chunk_text = " ".join(current_words)
        if chunk_text.strip():
            chunks.append(chunk_text)

    articoli = []
    for i, chunk_text in enumerate(chunks):
        articoli.append({
            "titolo": str(i + 1),
            "contenuto": chunk_text,
            "contenuto_parsato": [
                {
                    "titolo_articolo": str(i + 1),
                    "contenuto": chunk_text,
                    "contenuto_parsato_2": [],
                }
            ],
        })

    return articoli
