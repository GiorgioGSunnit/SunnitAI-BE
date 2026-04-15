"""
DocumentPart: the fundamental unit of the new parsing pipeline.

Part A fields are populated by the algorithmic parsers.
Part B fields (abstract, main_phrase, meaning, vector) are left None
and will be filled in by the AI enrichment step (Part B).
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class DocumentPart:
    # ── Part A: algorithmic ───────────────────────────────────────────────
    part_id: int
    """Sequential global identifier within the document."""

    sibling_of: Optional[int]
    """part_id of the immediately preceding part in the same section
    (older-brother ← younger-brother relationship, never parent-child)."""

    section_title: str
    """Title of the section this part belongs to."""

    page: int
    """0-based page number where this part starts."""

    bbox: list
    """Bounding box [x0, y0, x1, y1] on the page (floats).
    [0,0,0,0] when the source parser does not expose coordinates."""

    content: str
    """Plain text content (~1 000–1 200 characters, split at sentence boundary)."""

    char_count: int
    """len(content)."""

    font_name: Optional[str]
    """Dominant font name in this block (None for non-general parsers)."""

    font_size: Optional[float]
    """Dominant font size in pt (None for non-general parsers)."""

    place: dict
    """Position metadata:
    {
      "section_title": str,
      "page": int,
      "bbox": [x0, y0, x1, y1],
      "hierarchy": [title_level_1, title_level_2, ...]
    }
    """

    # ── Part B: AI-enriched (None until enrichment step runs) ─────────────
    abstract: Optional[str] = None
    """≤200-word description of this document part."""

    main_phrase: Optional[str] = None
    """The single most relevant sentence / core text."""

    meaning: Optional[str] = None
    """High-level semantic label: OBBLIGO | CONDIZIONE | TERMINE_TEMPORALE |
    SANZIONE | ALTRO  (same taxonomy as Analisi_Paragrafo)."""

    vector: Optional[list] = None
    """Dense embedding vector for semantic comparison (list of floats)."""

    # ── Tree level (set by Part B) ─────────────────────────────────────────
    level: str = "leaf"
    """Node level in the incremental summary tree:
    - "leaf"     : a raw document part produced by a parser
    - "section"  : synthesis of all leaf parts sharing the same section_title
    - "document" : synthesis of all section nodes (one per document)
    """

    children_ids: list = field(default_factory=list)
    """part_ids of the direct children of this node (populated for section and
    document nodes; empty for leaf nodes)."""

    def to_dict(self) -> dict:
        return asdict(self)
