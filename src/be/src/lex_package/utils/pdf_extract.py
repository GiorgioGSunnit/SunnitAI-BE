"""
Universal PDF page text extraction utility.
All parsers should use extract_page_text() instead of calling fitz directly
for text/table extraction, to ensure consistent handling across document types.
"""
import fitz


def _flatten_table(table_data: list) -> str:
    """Convert a list-of-lists table to readable plain text."""
    rows = []
    for row in table_data:
        cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if cells:
            rows.append("  ".join(cells))
    return "\n".join(rows)


def extract_page_text(
    page: fitz.Page,
    clip: fitz.Rect = None,
    ocr_text: str = None,
) -> str:
    """
    Extract all text from a page including tables, in reading order.
    Prose blocks and table blocks are both extracted and merged.
    Tables are flattened to readable text rather than serialized as Python lists.

    Args:
        page:     PyMuPDF page object
        clip:     optional rect to limit extraction area
        ocr_text: if provided, used instead of page.get_text() for prose extraction.
                  Pass this when the page was processed by OCR upstream.

    Returns:
        Clean plain text string with prose and table content merged.
    """
    parts = []

    if ocr_text:
        # OCR path — use pre-extracted text, still attempt table extraction
        if ocr_text.strip():
            parts.append(ocr_text.strip())
    else:
        # Normal path — extract prose blocks from PDF text layer
        kwargs = {"clip": clip} if clip else {}
        blocks = page.get_text("blocks", **kwargs)
        blocks = sorted(blocks, key=lambda b: (round(b[1] / 10) * 10, b[0]))
        for b in blocks:
            if len(b) > 4 and b[4].strip():
                parts.append(b[4].strip())

    # Table extraction runs regardless — even OCR'd PDFs may have
    # digitally encoded tables if the scan was processed with table detection
    try:
        tables = page.find_tables(clip=clip) if clip else page.find_tables()
        for table in tables:
            data = table.extract()
            if data:
                flat = _flatten_table(data)
                if flat.strip():
                    parts.append(flat)
    except Exception:
        pass

    return "\n".join(parts)


def _median_body_size(page: fitz.Page) -> float:
    """Median font size across all text spans on the page; baseline for title detection."""
    sizes = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("text", "").strip():
                    sizes.append(span.get("size", 0.0))
    if not sizes:
        return 11.0
    sizes.sort()
    return sizes[len(sizes) // 2]


def extract_page_blocks(
    page: fitz.Page,
    clip: fitz.Rect = None,
    ocr_text: str = None,
) -> list:
    """
    Extract page content as a list of dicts with type and text fields.
    Useful for parsers that need to process blocks individually rather than
    receiving a single merged string.

    Args:
        page:     PyMuPDF page object
        clip:     optional rect to limit extraction area
        ocr_text: if provided, used for prose content instead of page.get_text()

    Returns:
        List of dicts sorted in reading order:
        {"type": "text"|"title"|"table", "text": str, "bbox": tuple}
    """
    result = []

    if ocr_text:
        if ocr_text.strip():
            page_rect = page.rect
            result.append({
                "type": "text",
                "text": ocr_text.strip(),
                "bbox": (page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1),
            })
    else:
        body_size = _median_body_size(page)
        kwargs = {"clip": clip} if clip else {}
        page_dict = page.get_text("dict", **kwargs)
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            all_spans = [
                span
                for line in block.get("lines", [])
                for span in line.get("spans", [])
                if span.get("text", "").strip()
            ]
            if not all_spans:
                continue
            text = " ".join(s["text"].strip() for s in all_spans)
            if not text.strip():
                continue
            avg_size = sum(s.get("size", 0.0) for s in all_spans) / len(all_spans)
            is_bold = any(
                "bold" in s.get("font", "").lower() or bool(s.get("flags", 0) & 16)
                for s in all_spans
            )
            stripped = text.strip()
            if stripped == stripped.upper() and 3 < len(stripped) < 120:
                block_type = "title"
            elif avg_size > body_size * 1.2:
                block_type = "title"
            elif is_bold and avg_size >= body_size:
                block_type = "title"
            else:
                block_type = "text"
            result.append({
                "type": block_type,
                "text": stripped,
                "bbox": tuple(block["bbox"]),
            })

    try:
        tables = page.find_tables(clip=clip) if clip else page.find_tables()
        for table in tables:
            data = table.extract()
            if data:
                flat = _flatten_table(data)
                if flat.strip():
                    result.append({
                        "type": "table",
                        "text": flat,
                        "bbox": tuple(table.bbox),
                    })
    except Exception:
        pass

    # Sort all blocks in reading order: top-to-bottom, left-to-right
    return sorted(result, key=lambda x: (round(x["bbox"][1] / 10) * 10, x["bbox"][0]))
