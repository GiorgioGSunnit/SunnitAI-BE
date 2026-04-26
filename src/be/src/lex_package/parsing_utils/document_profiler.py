"""
Document profiler: single-pass feature extraction + JSON-driven template selection.

Template descriptors live in parsing_utils/templates/*.json.
Adding a new parser type requires only a new JSON file — no Python change.

Selection priority (A → B → C):
  A) User-supplied template hint (matched against each template's user_aliases).
  B) Score-based: extract features from the PDF, evaluate every template's
     scoring_rules, pick the highest normalised scorer above its threshold.
  C) Fallback heuristics → "regolamento", "indice", or "general" (box parser).
"""

import re
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

import fitz  # PyMuPDF

from .parser_banca import detect_start_page

# ─── Load template descriptors once at import time ────────────────────────────

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_templates() -> list[dict]:
    templates = []
    for json_file in sorted(_TEMPLATES_DIR.glob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            templates.append(json.load(f))
    # Sort by ascending priority (lower number = evaluated first)
    templates.sort(key=lambda t: t.get("priority", 99))
    return templates


_TEMPLATES: list[dict] = _load_templates()

# Index pattern needed for fallback heuristic (not part of any template)
_INDICE_ENTRY_RE = re.compile(
    r"\d{1,4}(?:\.\d{1,4})*\.?\s+.+?[.\u2026\u00B7\u2022•·∙]{2,}\s*\d{1,4}\b"
)
_COL_LETTERS = {"A", "B", "C", "D", "E", "F", "G"}

_SRCDIR = Path(__file__).resolve().parents[2]


# ─── Profile dataclass ────────────────────────────────────────────────────────

@dataclass
class DocumentProfile:
    pdf_path: str = ""
    pdf_name: str = ""
    page_count: int = 0
    # Table stats — consumed by ag_header_count_ge / table_page_ratio_gt rules
    pages_with_tables: int = 0
    pages_with_ag_header: int = 0
    table_page_ratio: float = 0.0
    # Fallback heuristic signals
    has_articolo_pattern: bool = False
    has_article_pattern: bool = False
    has_indice_keyword: bool = False
    has_indice_entries: bool = False
    # True when formal "Articolo N" numbering is found anywhere in the first 15 pages
    # (body-level detection, wider than head_text which only covers the first 3 pages)
    has_articolo_in_body: bool = False
    # Banca-specific: requires the parser's own page-boundary detection
    banca_start_page: int | None = None
    # The hint the user passed in (if any)
    template_hint: str | None = None
    # Results
    detected_type: str = ""
    confidence: float = 0.0
    scores: dict = field(default_factory=dict)
    # Full descriptor of the selected template (for downstream use)
    template_meta: dict = field(default_factory=dict)


# ─── Feature evaluation ───────────────────────────────────────────────────────

def _eval_rule(rule: dict, texts: dict, profile: DocumentProfile) -> int:
    """Return the points awarded by a single scoring rule, or 0."""
    feature = rule["feature"]
    points = rule.get("points", 0)
    flags = re.IGNORECASE | re.MULTILINE

    if feature == "regex_in_head":
        if re.search(rule["pattern"], texts["head"], flags):
            return points

    elif feature == "regex_in_extended":
        if re.search(rule["pattern"], texts["extended_lower"], flags):
            return points

    elif feature == "regex_in_broader":
        if re.search(rule["pattern"], texts["broader"], flags):
            return points

    elif feature == "regex_count_in_head":
        matches = re.findall(rule["pattern"], texts["head"], flags)
        if len(matches) >= rule.get("min_count", 1):
            return points

    elif feature == "filename_contains":
        filename = texts["filename"]
        if any(v in filename for v in rule.get("values", [])):
            return points

    elif feature == "table_page_ratio_gt":
        if profile.table_page_ratio > rule.get("value", 0):
            return points

    elif feature == "ag_header_count_ge":
        # Two rules can target different thresholds (e.g. ≥3 and ≥1);
        # award the highest applicable one — handled by caller summing all rules,
        # but these two rules are mutually exclusive by design in the JSON.
        if profile.pages_with_ag_header >= rule.get("value", 0):
            return points

    return 0


def _compute_scores(profile: DocumentProfile, texts: dict) -> dict[str, int]:
    """Evaluate every loaded template and return a scores dict."""
    scores: dict[str, int] = {}
    for tmpl in _TEMPLATES:
        total = 0
        for rule in tmpl.get("scoring_rules", []):
            total += _eval_rule(rule, texts, profile)
        scores[tmpl["id"]] = total
    return scores


# ─── Table scanning (needed before scoring) ───────────────────────────────────

def _is_ag_header_row(row) -> bool:
    if not row:
        return False
    non_empty = [c.strip().upper() for c in row if c and c.strip()]
    letters = {c for c in non_empty if len(c) == 1 and c in _COL_LETTERS}
    return len(letters) >= 4


def _scan_tables(doc, scan_pages: int) -> tuple[int, int, float]:
    """Return (pages_with_tables, pages_with_ag_header, table_page_ratio)."""
    sampled = set(range(scan_pages))
    if doc.page_count > 15:
        mid = doc.page_count // 2
        sampled.update([mid - 1, mid, mid + 1])

    pages_with_tables = 0
    pages_with_ag_header = 0

    for i in sampled:
        if i >= doc.page_count:
            continue
        tabs = doc[i].find_tables()
        if tabs.tables:
            pages_with_tables += 1
            for table in tabs.tables:
                data = table.extract()
                if data and _is_ag_header_row(data[0]):
                    pages_with_ag_header += 1
                    break

    ratio = pages_with_tables / len(sampled) if sampled else 0.0
    return pages_with_tables, pages_with_ag_header, ratio


# ─── Parser selection ─────────────────────────────────────────────────────────

def _match_hint(hint: str) -> str | None:
    """Try to match a user hint against all template user_aliases.
    Returns the template id if found, else None."""
    h = hint.lower().strip()
    # Exact match first
    for tmpl in _TEMPLATES:
        if h in [a.lower() for a in tmpl.get("user_aliases", [])]:
            return tmpl["id"]
    # Partial / substring match as fallback
    for tmpl in _TEMPLATES:
        if any(h in a.lower() or a.lower() in h for a in tmpl.get("user_aliases", [])):
            return tmpl["id"]
    return None


def _select_parser(profile: DocumentProfile) -> str:
    # ── A: User hint ──────────────────────────────────────────────────────
    if profile.template_hint:
        matched = _match_hint(profile.template_hint)
        if matched:
            print(f"[INFO] Template forced by user hint '{profile.template_hint}' → {matched}")
            return matched
        print(f"[WARN] Template hint '{profile.template_hint}' did not match any template, falling back to scoring")

    # ── B: Score-based ────────────────────────────────────────────────────
    scores = profile.scores
    best_type: str | None = None
    best_normalised: float = -1.0

    for tmpl in _TEMPLATES:
        tid = tmpl["id"]
        score = scores.get(tid, 0)
        threshold = tmpl.get("threshold", 0)
        max_score = tmpl.get("max_score", 1)

        if score < threshold:
            continue

        # Special case: banca also requires the start-page detector to succeed
        if tid == "banca" and profile.banca_start_page is None and score < threshold + 1:
            continue

        normalised = score / max_score
        if normalised > best_normalised:
            best_normalised = normalised
            best_type = tid

    if best_type:
        # Override: banca selected but its score exceeds its own max_score — this
        # means the filename is artificially inflating the score (e.g. filename
        # contains "banca"/"disposizioni"/"avc" giving +3 even for non-circular docs).
        # When this happens AND the document has formal "Articolo N" article numbering
        # in the body, it is a regolamento-style document, not a banca circular.
        # We do NOT override when banca score ≤ max_score — in that case the content
        # itself supports the banca classification (e.g. EXT_1_1-type documents).
        if best_type == "banca" and profile.has_articolo_in_body:
            banca_tmpl = next((t for t in _TEMPLATES if t["id"] == "banca"), None)
            banca_max_score = banca_tmpl.get("max_score", 1) if banca_tmpl else 1
            banca_score = scores.get("banca", 0)
            if banca_score > banca_max_score:
                regolamento_score = scores.get("regolamento", 0)
                regolamento_tmpl = next((t for t in _TEMPLATES if t["id"] == "regolamento"), None)
                reg_threshold = regolamento_tmpl.get("threshold", 0) if regolamento_tmpl else 0
                if regolamento_score >= reg_threshold:
                    print(
                        f"[INFO] banca score ({banca_score}) exceeds max_score ({banca_max_score}) "
                        f"and formal 'Articolo N' numbering found in body "
                        f"(regolamento score={regolamento_score} ≥ threshold={reg_threshold}) "
                        f"→ overriding banca → regolamento"
                    )
                    best_type = "regolamento"
        return best_type

    # ── C: Fallback heuristics ────────────────────────────────────────────
    if profile.banca_start_page is not None:
        return "banca"
    if profile.has_articolo_pattern or profile.has_article_pattern:
        return "regolamento"
    if profile.has_indice_keyword and profile.has_indice_entries:
        return "indice"
    return "general"


# ─── Public API ───────────────────────────────────────────────────────────────

def profile_document(
    pdf_path: str,
    pdf_name: str = "",
    template_hint: str | None = None,
) -> DocumentProfile:
    """
    Open *pdf_path*, extract features in a single pass, select the best
    template, and return a populated DocumentProfile.

    Args:
        pdf_path: Absolute path to the PDF.
        pdf_name: Optional filename override (used for filename-based rules).
        template_hint: Optional user-supplied hint (e.g. "boe", "banca").
    """
    if not pdf_name:
        pdf_name = Path(pdf_path).name

    doc = fitz.open(pdf_path)
    try:
        profile = _build_profile(doc, pdf_name, template_hint)
    finally:
        doc.close()

    profile.pdf_path = str(pdf_path)
    profile.pdf_name = pdf_name

    try:
        profile.banca_start_page = detect_start_page(str(pdf_path))
    except Exception:
        profile.banca_start_page = None

    # Recompute scores now that banca_start_page is known
    # (we do it again here so table stats are already in profile)
    profile.detected_type = _select_parser(profile)
    _set_confidence(profile)
    _set_template_meta(profile)
    _save_profile(profile)
    return profile


def _build_profile(doc, pdf_name: str, template_hint: str | None) -> DocumentProfile:
    profile = DocumentProfile(page_count=doc.page_count)
    profile.template_hint = template_hint

    name_lower = (pdf_name or "").lower()
    head_pages = min(3, doc.page_count)
    head_text = "\n".join(doc[i].get_text() for i in range(head_pages))
    extended_pages = min(8, doc.page_count)
    extended_lower = "\n".join(doc[i].get_text() for i in range(extended_pages)).lower()
    scan_pages = min(15, doc.page_count)
    broader_text = "\n".join(doc[i].get_text() for i in range(scan_pages))

    texts = {
        "head": head_text,
        "extended_lower": extended_lower,
        "broader": broader_text,
        "filename": name_lower,
    }

    # Table stats (required before scoring)
    profile.pages_with_tables, profile.pages_with_ag_header, profile.table_page_ratio = (
        _scan_tables(doc, scan_pages)
    )

    # Fallback heuristic signals
    profile.has_articolo_pattern = bool(re.search(r"\bArticolo\s+\d+\b", head_text))
    profile.has_article_pattern = bool(re.search(r"\bArticle\s+\d+\b", head_text))
    profile.has_articolo_in_body = bool(re.search(r"\bArticolo\s+\d+\b", broader_text, re.IGNORECASE))
    indice_text = "\n".join(doc[i].get_text() for i in range(min(2, doc.page_count)))
    profile.has_indice_keyword = bool(re.search(r"\bindice\b", indice_text, re.IGNORECASE))
    profile.has_indice_entries = bool(_INDICE_ENTRY_RE.search(indice_text))

    # Score all templates
    profile.scores = _compute_scores(profile, texts)

    return profile


def _set_confidence(profile: DocumentProfile) -> None:
    detected = profile.detected_type
    tmpl = next((t for t in _TEMPLATES if t["id"] == detected), None)
    if tmpl:
        max_s = tmpl.get("max_score", 1)
        profile.confidence = min(1.0, profile.scores.get(detected, 0) / max_s)
    elif detected in ("regolamento", "indice", "general"):
        profile.confidence = 0.5
    else:
        profile.confidence = 0.0


def _set_template_meta(profile: DocumentProfile) -> None:
    detected = profile.detected_type
    tmpl = next((t for t in _TEMPLATES if t["id"] == detected), None)
    if tmpl:
        # Include the full descriptor minus scoring_rules (those are internal)
        profile.template_meta = {k: v for k, v in tmpl.items() if k != "scoring_rules"}
    else:
        profile.template_meta = {"id": detected}


def _save_profile(profile: DocumentProfile) -> None:
    log_dir = Path(_SRCDIR / "out_parser")
    log_dir.mkdir(parents=True, exist_ok=True)
    profile_path = log_dir / "document_profile.json"
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(asdict(profile), f, ensure_ascii=False, indent=2)
