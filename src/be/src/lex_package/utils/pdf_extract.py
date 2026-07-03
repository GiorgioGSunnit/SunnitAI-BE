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
        if blocks:
            # Use actual page width, not max block x2 (which underestimates
            # and shifts the midpoint, breaking two-column detection)
            page_width = page.rect.width if page.rect.width > 0 else (
                max(b[2] for b in blocks) if blocks else 0
            )
            if page_width > 0:
                page_mid = page_width / 2
                left = [b for b in blocks if (b[0] + b[2]) / 2 < page_mid]
                right = [b for b in blocks if (b[0] + b[2]) / 2 >= page_mid]
                # Lower floor threshold from 0.25 to 0.20 — a page with
                # 5 left and 16 right blocks (ratio=0.24) is still two-column
                if (len(left) >= 3 and len(right) >= 3 and
                        0.20 <= len(left) / len(blocks) <= 0.80):
                    left_sorted = sorted(left, key=lambda b: (round(b[1] / 10) * 10, b[0]))
                    right_sorted = sorted(right, key=lambda b: (round(b[1] / 10) * 10, b[0]))
                    blocks = left_sorted + right_sorted
                else:
                    blocks = sorted(blocks, key=lambda b: (round(b[1] / 10) * 10, b[0]))
            else:
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


def _detect_columns(blocks: list, page_width: float) -> int:
    """
    Detect number of columns on a page by analysing x-coordinate
    distribution of blocks.
    Returns 1 for single column, 2 for two-column layout.
    """
    if not blocks or page_width <= 0:
        return 1

    # Collect x-midpoints of all blocks
    midpoints = [(b["bbox"][0] + b["bbox"][2]) / 2 for b in blocks]
    if not midpoints:
        return 1

    page_mid = page_width / 2
    left_count = sum(1 for m in midpoints if m < page_mid)
    right_count = sum(1 for m in midpoints if m >= page_mid)

    # Two-column if blocks are roughly evenly split between
    # left and right halves, with at least 3 blocks per side
    if (left_count >= 3 and right_count >= 3 and
            0.25 <= left_count / len(midpoints) <= 0.75):
        return 2
    return 1


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
    if not result:
        return result

    # Get page width from the rightmost bbox edge seen
    page_width = max(b["bbox"][2] for b in result)
    num_columns = _detect_columns(result, page_width)

    if num_columns == 2:
        page_mid = page_width / 2
        left_blocks = [b for b in result if (b["bbox"][0] + b["bbox"][2]) / 2 < page_mid]
        right_blocks = [b for b in result if (b["bbox"][0] + b["bbox"][2]) / 2 >= page_mid]

        # Sort each column top-to-bottom independently
        left_sorted = sorted(left_blocks, key=lambda x: (round(x["bbox"][1] / 10) * 10, x["bbox"][0]))
        right_sorted = sorted(right_blocks, key=lambda x: (round(x["bbox"][1] / 10) * 10, x["bbox"][0]))

        return left_sorted + right_sorted

    # Single column — original sort
    return sorted(result, key=lambda x: (round(x["bbox"][1] / 10) * 10, x["bbox"][0]))
