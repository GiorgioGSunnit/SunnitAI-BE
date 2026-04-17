"""
Part B: AI enrichment of DocumentPart objects.

Runs a three-phase, bottom-up pipeline on the flat ``parts`` list produced
by the Part-A parsers:

  Phase 1 – Leaf enrichment
    Each leaf part receives:
      abstract    ≤200-word AI description of its raw content
      main_phrase the single most relevant sentence (core text)
      meaning     OBBLIGO | CONDIZIONE | TERMINE_TEMPORALE | SANZIONE | ALTRO
      vector      dense embedding computed locally (no extra LLM call)

  Phase 2 – Section synthesis
    One new synthesis node is created for each unique section_title.
    Its abstract is an AI-generated summary of the children's abstracts
    (not the raw text), keeping token consumption proportional to the
    number of parts, not the document size.

  Phase 3 – Document synthesis
    One top-level node is created from the section-node abstracts.

All AI calls use the same primary→fallback deployment strategy as
analisi_parallel.py (async batch + RateLimitError routing).
Vectors are computed with the existing embed_text() utility.
"""

import json
import logging
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from openai import RateLimitError, APITimeoutError, ContentFilterFinishReasonError

from lex_package.llm.factory import build_chat_model
from lex_package.t.part_enrichment import PartEnrichment, SectionSynthesis
from lex_package.utils.embeddings import embed_text
from lex_package.utils.utils import load_prompt

logger = logging.getLogger("lex_package.arricchimento_parti")

MAX_CONCURRENCY = 5

# ─── Lazy LLM init ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _llm_primary():
    return build_chat_model(target="primary", temperature=0)

@lru_cache(maxsize=1)
def _llm_fallback():
    return build_chat_model(target="fallback", temperature=0)

@lru_cache(maxsize=1)
def _structured_leaf():
    base = _llm_primary()
    return base.with_structured_output(PartEnrichment).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )

@lru_cache(maxsize=1)
def _structured_leaf_fallback():
    base = _llm_fallback()
    return base.with_structured_output(PartEnrichment).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )

@lru_cache(maxsize=1)
def _structured_synthesis():
    base = _llm_primary()
    return base.with_structured_output(SectionSynthesis).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )

@lru_cache(maxsize=1)
def _structured_synthesis_fallback():
    base = _llm_fallback()
    return base.with_structured_output(SectionSynthesis).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


# ─── Fallback batch helper (mirrors analisi_parallel) ─────────────────────────

async def _batch_with_fallback(primary_llm, fallback_llm, messages_list, cfg):
    try:
        return await primary_llm.abatch(messages_list, config=cfg)
    except RateLimitError as e:
        delay = None
        if getattr(e, "response", None):
            try:
                delay = float(e.response.headers.get("Retry-After", 0))
            except (TypeError, ValueError):
                pass
        if delay and delay > 1:
            logger.warning("[Retry-After=%.0fs] switching to fallback deployment", delay)
            return await fallback_llm.abatch(messages_list, config=cfg)
        raise


async def _batch_safe(primary_llm, fallback_llm, messages_list, cfg, default_factory):
    """Like _batch_with_fallback but handles ContentFilterFinishReasonError item-by-item."""
    try:
        return await _batch_with_fallback(primary_llm, fallback_llm, messages_list, cfg)
    except ContentFilterFinishReasonError:
        logger.warning("Content filter on batch – falling back to one-by-one processing")
        results = []
        for i, msg in enumerate(messages_list):
            try:
                r = await primary_llm.ainvoke(msg)
                results.append(r)
            except (ContentFilterFinishReasonError, Exception) as e:
                logger.warning("Item %d failed (%s), using default", i, e)
                results.append(default_factory())
        return results


# ─── Prompt loaders ───────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _leaf_system():
    return load_prompt("part_enrichment_system.txt")

@lru_cache(maxsize=1)
def _leaf_user_template():
    return load_prompt("part_enrichment_user.txt")

@lru_cache(maxsize=1)
def _synthesis_system():
    return load_prompt("section_synthesis_system.txt")

@lru_cache(maxsize=1)
def _synthesis_user_template():
    return load_prompt("section_synthesis_user.txt")


def _build_leaf_messages(part: dict, document_name: str) -> list:
    hierarchy = (part.get("place") or {}).get("hierarchy") or []
    user_text = _leaf_user_template().format(
        document_name=document_name,
        section_title=part.get("section_title") or "",
        page=(part.get("page") or 0) + 1,   # 1-based for humans
        hierarchy=" › ".join(hierarchy) if hierarchy else "—",
        content=part.get("content") or "",
    )
    return [SystemMessage(content=_leaf_system()), HumanMessage(content=user_text)]


def _build_synthesis_messages(
    section_title: str,
    document_name: str,
    children_abstracts: list[str],
) -> list:
    numbered = "\n\n".join(
        f"[{i+1}] {a}" for i, a in enumerate(children_abstracts)
    )
    user_text = _synthesis_user_template().format(
        document_name=document_name,
        section_title=section_title,
        parts_count=len(children_abstracts),
        children_abstracts=numbered,
    )
    return [SystemMessage(content=_synthesis_system()), HumanMessage(content=user_text)]


# ─── Default fallback objects ──────────────────────────────────────────────────

def _default_leaf() -> PartEnrichment:
    return PartEnrichment(
        abstract="[Analisi non disponibile]",
        main_phrase="",
        meaning="altro",
    )

def _default_synthesis() -> SectionSynthesis:
    return SectionSynthesis(
        abstract="[Sintesi non disponibile]",
        main_phrase="",
        meaning="altro",
    )


# ─── Phase 1: Leaf enrichment ─────────────────────────────────────────────────

async def _enrich_leaves(parts: list[dict], document_name: str) -> None:
    """Enrich all leaf parts in-place (abstract, main_phrase, meaning, vector).

    Parts that already have a non-null abstract are skipped (idempotent).
    """
    to_enrich = [
        (i, p) for i, p in enumerate(parts)
        if p.get("level", "leaf") == "leaf" and p.get("abstract") is None
        and (p.get("content") or "").strip()
    ]

    if not to_enrich:
        logger.info("No leaf parts to enrich (all already enriched or empty)")
        return

    logger.info("Enriching %d leaf parts…", len(to_enrich))

    indices, leaf_parts = zip(*to_enrich)
    messages_list = [_build_leaf_messages(p, document_name) for p in leaf_parts]

    cfg = RunnableConfig(max_concurrency=MAX_CONCURRENCY)
    results = await _batch_safe(
        _structured_leaf(),
        _structured_leaf_fallback(),
        messages_list,
        cfg,
        _default_leaf,
    )

    for idx, result in zip(indices, results):
        p = parts[idx]
        p["abstract"] = result.abstract
        p["main_phrase"] = result.main_phrase
        p["meaning"] = result.meaning.value if hasattr(result.meaning, "value") else str(result.meaning)
        # Vector: computed locally from raw content (no extra LLM call)
        p["vector"] = embed_text(p.get("content") or "")

    logger.info("Leaf enrichment complete")


# ─── Phase 2: Section synthesis ───────────────────────────────────────────────

async def _synthesize_sections(
    leaf_parts: list[dict],
    document_name: str,
    next_part_id: int,
) -> list[dict]:
    """Create one synthesis node per unique section_title.

    Only sections with at least one enriched (abstract != None) leaf part
    are synthesised.  Returns the list of new section nodes.
    """
    # Group leaf parts by section_title (preserve order of first appearance)
    groups: dict[str, list[dict]] = defaultdict(list)
    order: list[str] = []
    for p in leaf_parts:
        if p.get("level", "leaf") != "leaf":
            continue
        title = p.get("section_title") or "Documento"
        if title not in groups:
            order.append(title)
        groups[title].append(p)

    # Build message list only for sections that have abstracts
    buildable: list[tuple[str, list[str], list[int]]] = []
    for title in order:
        children = groups[title]
        abstracts = [c["abstract"] for c in children if c.get("abstract")]
        if not abstracts:
            continue
        child_ids = [c["part_id"] for c in children]
        buildable.append((title, abstracts, child_ids))

    if not buildable:
        return []

    logger.info("Synthesising %d sections…", len(buildable))

    messages_list = [
        _build_synthesis_messages(title, document_name, abstracts)
        for title, abstracts, _ in buildable
    ]
    cfg = RunnableConfig(max_concurrency=MAX_CONCURRENCY)
    results = await _batch_safe(
        _structured_synthesis(),
        _structured_synthesis_fallback(),
        messages_list,
        cfg,
        _default_synthesis,
    )

    section_nodes: list[dict] = []
    for (title, abstracts, child_ids), result in zip(buildable, results):
        next_part_id += 1
        node = {
            "part_id": next_part_id,
            "sibling_of": None,
            "section_title": title,
            "page": groups[title][0].get("page", 0),
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "content": "",
            "char_count": 0,
            "font_name": None,
            "font_size": None,
            "place": {
                "section_title": title,
                "page": groups[title][0].get("page", 0),
                "bbox": [0.0, 0.0, 0.0, 0.0],
                "hierarchy": (groups[title][0].get("place") or {}).get("hierarchy", [])[:1],
            },
            "level": "section",
            "children_ids": child_ids,
            # Part B fields
            "abstract": result.abstract,
            "main_phrase": result.main_phrase,
            "meaning": result.meaning.value if hasattr(result.meaning, "value") else str(result.meaning),
            "vector": embed_text(result.abstract),
        }
        section_nodes.append(node)

    logger.info("Section synthesis complete: %d nodes", len(section_nodes))
    return section_nodes


# ─── Phase 3: Document synthesis ──────────────────────────────────────────────

async def _synthesize_document(
    section_nodes: list[dict],
    document_name: str,
    next_part_id: int,
) -> dict:
    """Create a single document-level synthesis node from the section abstracts."""
    abstracts = [n["abstract"] for n in section_nodes if n.get("abstract")]
    child_ids = [n["part_id"] for n in section_nodes]

    if not abstracts:
        logger.warning("No section abstracts available for document synthesis, using placeholder")
        result = _default_synthesis()
    else:
        logger.info("Synthesising document node from %d section abstracts…", len(abstracts))
        messages = _build_synthesis_messages(document_name, document_name, abstracts)
        try:
            result = await _structured_synthesis().ainvoke(messages)
        except Exception as e:
            logger.warning("Document synthesis failed (%s), using placeholder", e)
            result = _default_synthesis()

    return {
        "part_id": next_part_id + 1,
        "sibling_of": None,
        "section_title": document_name,
        "page": 0,
        "bbox": [0.0, 0.0, 0.0, 0.0],
        "content": "",
        "char_count": 0,
        "font_name": None,
        "font_size": None,
        "place": {
            "section_title": document_name,
            "page": 0,
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "hierarchy": [document_name],
        },
        "level": "document",
        "children_ids": child_ids,
        "abstract": result.abstract,
        "main_phrase": result.main_phrase,
        "meaning": result.meaning.value if hasattr(result.meaning, "value") else str(result.meaning),
        "vector": embed_text(result.abstract),
    }


# ─── Public API ───────────────────────────────────────────────────────────────

async def arricchisci_parti(
    parts: list[dict],
    document_name: str,
    output_file_path_str: str | None = None,
) -> list[dict]:
    """
    Run the full Part B enrichment pipeline on *parts*.

    Args:
        parts: The flat DocumentPart list from parse() output (level="leaf").
        document_name: Document name used in prompts and the document synthesis node.
        output_file_path_str: If given, write the enriched JSON to this path.

    Returns:
        The input list (enriched in-place) plus appended section and document
        synthesis nodes.
    """
    if not parts:
        logger.warning("arricchisci_parti: empty parts list, nothing to do")
        return parts

    # Phase 1: Leaf enrichment
    await _enrich_leaves(parts, document_name)

    # Determine next available part_id
    max_id = max((p.get("part_id") or 0 for p in parts), default=0)

    # Phase 2: Section synthesis
    section_nodes = await _synthesize_sections(parts, document_name, next_part_id=max_id)
    max_id += len(section_nodes)

    # Phase 3: Document synthesis
    doc_node = await _synthesize_document(section_nodes, document_name, next_part_id=max_id)

    all_parts = parts + section_nodes + [doc_node]

    if output_file_path_str:
        _save_enriched(all_parts, output_file_path_str)

    logger.info(
        "arricchisci_parti done: %d leaf, %d section, 1 document node",
        len(parts), len(section_nodes),
    )
    return all_parts


def _save_enriched(all_parts: list[dict], path_str: str) -> None:
    p = Path(path_str)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"parts": all_parts}, f, ensure_ascii=False, indent=2)
    logger.info("Enriched parts saved to %s", p)


# ─── Load / merge helper for CLI ──────────────────────────────────────────────

def load_parts_from_parse_output(json_path: str) -> list[dict]:
    """Load the parts list from a parse() output JSON file."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    parts = data.get("parts", [])
    if not parts:
        raise ValueError(
            f"No 'parts' key found in {json_path}. "
            "Run --parse first to generate the parts list."
        )
    return parts


def merge_enriched_into_parse_output(parse_json_path: str, all_parts: list[dict]) -> None:
    """Write enriched parts back into the original parse output JSON (in-place update)."""
    with open(parse_json_path, encoding="utf-8") as f:
        data = json.load(f)
    data["parts"] = all_parts
    with open(parse_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Merged enriched parts back into %s", parse_json_path)
