"""
General-purpose parser.

Used as the final fallback when no specialised template scores above its
threshold.  The document is decomposed into DocumentPart objects using a
purely algorithmic, AI-free approach:

  1. Each page is read as a set of "boxes" (PyMuPDF blocks with font metadata).
  2. Repeated headers / footers are filtered out.
  3. Blocks whose dominant font is significantly larger or bolder than the
     body median are treated as section titles.
  4. Text is accumulated until it exceeds SPLIT_THRESHOLD characters, then
     split at the first sentence boundary ("." ) between SPLIT_THRESHOLD and
     SPLIT_MAX.
  5. Parts in the same section are linked with a sibling_of pointer
     (older-brother → younger-brother), never with a parent-child relation.
  6. Each part carries full position metadata: section title, page, bbox,
     and the title hierarchy stack at the time of creation.

Returns a list of dicts (DocumentPart.to_dict()) so the caller does not need
to import the dataclass.
"""

import re
from collections import Counter
from statistics import median
from pathlib import Path

import fitz  # PyMuPDF

from .document_part import DocumentPart
from .parser_banca import identify_repeated_headers_footers

# ─── Splitting constants ───────────────────────────────────────────────────────

SPLIT_THRESHOLD = 10_000   # start looking for a split point here
SPLIT_MAX = 12_000         # hard cap: force-split even without a period
_SENTENCE_END_RE = re.compile(r"[.!?]\s")


# ─── Font / title helpers ──────────────────────────────────────────────────────

def _compute_body_font_size(doc: fitz.Document, sample_pages: int = 5) -> float:
    """Return the median font size across the first *sample_pages* pages."""
    sizes: list[float] = []
    for i in range(min(sample_pages, doc.page_count)):
        page = doc[i]
        d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text and len(text) > 2:
                        sizes.append(span.get("size", 0.0))
    if not sizes:
        return 11.0
    return median(sizes)


def _is_title_block(block: dict, body_size: float) -> bool:
    """Heuristic: a block is a title if it is short, large, or bold."""
    lines = block.get("lines", [])
    if not lines:
        return False

    all_text = " ".join(
        span.get("text", "")
        for line in lines
        for span in line.get("spans", [])
    ).strip()

    if not all_text or len(all_text) > 200:
        return False

    sizes: list[float] = []
    flags_list: list[int] = []
    for line in lines:
        for span in line.get("spans", []):
            t = span.get("text", "").strip()
            if t:
                sizes.append(span.get("size", 0.0))
                flags_list.append(span.get("flags", 0))

    if not sizes:
        return False

    avg_size = sum(sizes) / len(sizes)
    is_large = avg_size >= body_size * 1.15
    is_bold = any(f & 16 for f in flags_list)          # bit 4 = bold in MuPDF
    is_short = len(all_text) < 120
    is_allcaps = all_text == all_text.upper() and len(all_text) > 3

    return (is_large and is_short) or (is_bold and is_short) or (is_allcaps and is_short)


def _block_text_and_meta(block: dict) -> tuple[str, str | None, float | None]:
    """Return (plain_text, dominant_font, dominant_size) for a text block."""
    texts: list[str] = []
    fonts: list[str] = []
    sizes: list[float] = []

    for line in block.get("lines", []):
        for span in line.get("spans", []):
            t = span.get("text", "").strip()
            if t:
                texts.append(t)
                fonts.append(span.get("font", ""))
                sizes.append(span.get("size", 0.0))

    plain = " ".join(texts).strip()
    dominant_font = Counter(fonts).most_common(1)[0][0] if fonts else None
    dominant_size = sum(sizes) / len(sizes) if sizes else None
    return plain, dominant_font, dominant_size


# ─── Splitting helper ─────────────────────────────────────────────────────────

def _split_at_boundary(text: str) -> tuple[str, str]:
    """
    Given text longer than SPLIT_THRESHOLD, return (chunk, remainder) where
    chunk ends at the first sentence boundary between SPLIT_THRESHOLD and
    SPLIT_MAX, or at SPLIT_MAX if none is found.
    """
    search_start = SPLIT_THRESHOLD
    search_end = min(SPLIT_MAX, len(text))

    m = _SENTENCE_END_RE.search(text, search_start, search_end)
    if m:
        cut = m.start() + 1   # include the punctuation mark
    else:
        # no sentence boundary found in window → cut at SPLIT_MAX or end
        cut = search_end

    return text[:cut].strip(), text[cut:].strip()


# ─── Main parser ──────────────────────────────────────────────────────────────

def parser_general(pdf_path: str) -> list[dict]:
    """
    Parse *pdf_path* and return a list of DocumentPart dicts.

    The list is ordered document-sequentially.  Parts belonging to the same
    section are linked via sibling_of (previous part's part_id); a new section
    resets the chain (sibling_of = None for the first part of each section).
    """
    doc = fitz.open(pdf_path)
    repeated_raw = identify_repeated_headers_footers(doc)
    repeated_set = {r.strip().lower() for r in repeated_raw if r.strip()}
    body_size = _compute_body_font_size(doc)

    # ── Collect all blocks across all pages ───────────────────────────────
    raw_blocks: list[dict] = []   # {text, is_title, bbox, font, size, page}

    for page_num in range(doc.page_count):
        page = doc[page_num]
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        height = page.rect.height

        # Sort blocks top-to-bottom, left-to-right
        blocks = sorted(
            (b for b in page_dict.get("blocks", []) if b.get("type") == 0),
            key=lambda b: (round(b["bbox"][1] / 5) * 5, b["bbox"][0]),
        )

        for block in blocks:
            plain, font, size = _block_text_and_meta(block)
            if not plain:
                continue

            # Skip repeated headers / footers
            if plain.strip().lower() in repeated_set:
                continue
            # Skip lone page numbers
            if re.fullmatch(r"\d{1,4}", plain.strip()):
                continue

            y0 = block["bbox"][1]
            is_hf = (y0 < height * 0.10) or (y0 > height * 0.90)
            if is_hf and len(plain) < 80:
                continue

            raw_blocks.append({
                "text": plain,
                "is_title": _is_title_block(block, body_size),
                "bbox": list(block["bbox"]),
                "font": font,
                "size": size,
                "page": page_num,
            })

    doc.close()

    # ── Build DocumentPart list ────────────────────────────────────────────
    parts: list[dict] = []
    part_id = 0
    section_title = ""
    hierarchy: list[str] = []
    last_part_id_in_section: int | None = None

    # Text accumulation buffer
    buf_text = ""
    buf_bbox: list[float] | None = None
    buf_font: str | None = None
    buf_size: float | None = None
    buf_page: int = 0

    def _flush(force: bool = False) -> None:
        """Emit one or more DocumentParts from the current buffer."""
        nonlocal buf_text, buf_bbox, buf_font, buf_size, buf_page
        nonlocal part_id, last_part_id_in_section

        text = buf_text.strip()
        if not text:
            buf_text = ""
            return

        # Split repeatedly if the buffer is still too long
        while len(text) > SPLIT_THRESHOLD:
            chunk, text = _split_at_boundary(text)
            if not chunk:
                break
            part_id += 1
            parts.append(
                DocumentPart(
                    part_id=part_id,
                    sibling_of=last_part_id_in_section,
                    section_title=section_title,
                    page=buf_page,
                    bbox=buf_bbox or [0.0, 0.0, 0.0, 0.0],
                    content=chunk,
                    char_count=len(chunk),
                    font_name=buf_font,
                    font_size=buf_size,
                    place={
                        "section_title": section_title,
                        "page": buf_page,
                        "bbox": buf_bbox or [0.0, 0.0, 0.0, 0.0],
                        "hierarchy": list(hierarchy),
                    },
                ).to_dict()
            )
            last_part_id_in_section = part_id

        # Remaining text (< SPLIT_THRESHOLD) — only flush if forced or non-empty remainder
        if text and (force or len(text) >= 50):
            part_id += 1
            parts.append(
                DocumentPart(
                    part_id=part_id,
                    sibling_of=last_part_id_in_section,
                    section_title=section_title,
                    page=buf_page,
                    bbox=buf_bbox or [0.0, 0.0, 0.0, 0.0],
                    content=text,
                    char_count=len(text),
                    font_name=buf_font,
                    font_size=buf_size,
                    place={
                        "section_title": section_title,
                        "page": buf_page,
                        "bbox": buf_bbox or [0.0, 0.0, 0.0, 0.0],
                        "hierarchy": list(hierarchy),
                    },
                ).to_dict()
            )
            last_part_id_in_section = part_id
            text = ""

        buf_text = text  # leftover (< 50 chars) stays in buffer
        if not buf_text:
            buf_bbox = None

    for block in raw_blocks:
        if block["is_title"]:
            # New section: flush current buffer, reset sibling chain
            _flush(force=True)
            section_title = block["text"]
            hierarchy = _update_hierarchy(hierarchy, block["text"], block.get("size") or body_size, body_size)
            last_part_id_in_section = None
            buf_text = ""
            buf_bbox = None
        else:
            # Accumulate body text
            joined = (buf_text + " " + block["text"]).strip() if buf_text else block["text"]
            if buf_bbox is None:
                buf_bbox = block["bbox"]
                buf_font = block["font"]
                buf_size = block["size"]
                buf_page = block["page"]
            buf_text = joined

            # Eagerly flush if buffer is well over the split max
            if len(buf_text) > SPLIT_MAX * 1.5:
                _flush()

    # Final flush
    _flush(force=True)

    return parts


# ─── Hierarchy helpers ────────────────────────────────────────────────────────

def _update_hierarchy(
    current: list[str],
    new_title: str,
    new_size: float,
    body_size: float,
) -> list[str]:
    """
    Maintain a simple title hierarchy stack.
    Large titles (≥ body*1.4) are treated as top-level; others are nested.
    This is a best-effort heuristic — PDF documents have no guaranteed heading levels.
    """
    if new_size >= body_size * 1.4:
        return [new_title]
    if new_size >= body_size * 1.2:
        # Second-level: keep first-level ancestor if any
        if current:
            return [current[0], new_title]
        return [new_title]
    # Third-level or uncategorised: append
    base = current[:2] if len(current) >= 2 else current[:]
    base.append(new_title)
    return base


# ─── Compatibility bridge ─────────────────────────────────────────────────────

def parts_to_articoli(parts: list[dict]) -> list[dict]:
    """
    Convert a DocumentPart list to the legacy ``articoli`` envelope expected
    by analisi.py and flatten.py.

    Each unique section_title becomes one "articolo".  Parts within a section
    are concatenated into contenuto, and also kept individually in
    contenuto_parsato (with part_id / sibling_of metadata preserved).
    """
    sections: dict[str, dict] = {}
    order: list[str] = []

    for part in parts:
        title = part.get("section_title") or "Documento"
        if title not in sections:
            sections[title] = {
                "identificativo": str(len(order) + 1),
                "titolo": title,
                "page": part["page"],
                "contenuto": "",
                "contenuto_parsato": [],
            }
            order.append(title)

        sec = sections[title]
        sec["contenuto"] = (sec["contenuto"] + " " + part["content"]).lstrip()
        sec["contenuto_parsato"].append({
            "identificativo": str(part["part_id"]),
            "titolo_articolo": title,
            "contenuto": part["content"],
            "part_id": part["part_id"],
            "sibling_of": part["sibling_of"],
        })

    return [sections[t] for t in order]
