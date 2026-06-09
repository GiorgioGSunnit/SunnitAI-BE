import logging
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from openai import RateLimitError, APITimeoutError, ContentFilterFinishReasonError, InternalServerError

from lex_package.utils.runtime_checks import lex_package_is_installed
from lex_package.utils.utils import load_prompt
from lex_package.t.analisi_articolo import Analisi_Paragrafo
from lex_package.llm.factory import build_chat_model
from lex_package.utils.embeddings import embed_text
from lex_package.utils.normalize_articoli_tree import (
    content_ok_for_llm,
    normalizza_gerarchia_articoli,
)

# Set up logger for this module
logger = logging.getLogger("lex_package.analisi_parallel")


# --- Lazy initialization per evitare connessione ad Azure all'import --------
# Questo permette di usare `lex-cli -h` senza credenziali configurate.

@lru_cache(maxsize=1)
def _get_llm():
    """Lazy-init del modello primario."""
    return build_chat_model(target="primary", temperature=0)


@lru_cache(maxsize=1)
def _get_llm_fallback():
    """Lazy-init del modello fallback."""
    return build_chat_model(target="fallback", temperature=0)


def _build_structured(llm_base):
    """Return the LLM wrapped with structured output.
    No retry wrapper — the call loop handles fallback manually per-chunk.
    """
    return llm_base.with_structured_output(Analisi_Paragrafo)


@lru_cache(maxsize=1)
def _get_structured_llm():
    """Lazy-init dello structured LLM primario."""
    return _build_structured(_get_llm())


@lru_cache(maxsize=1)
def _get_structured_llm_fallback():
    """Lazy-init dello structured LLM fallback."""
    return _build_structured(_get_llm_fallback())


# ---------------------------------------------------------------------------
# Chunk size — max words per LLM call to stay safely within the 90s timeout.
# Based on observed throughput (~57 words/s), 400 words ≈ 7s per call.
# Increase if you want fewer calls on longer documents; decrease for tighter
# timeout safety. Re-enable truncation below if you need a hard cap instead.
# ---------------------------------------------------------------------------
MAX_CHUNK_WORDS = 250

# ---------------------------------------------------------------------------
# Pattern priority for merging chunk results — higher wins.
# ---------------------------------------------------------------------------
_PATTERN_PRIORITY = {
    "sanzione": 5,
    "obbligo": 4,
    "termine temporale": 3,
    "condizione": 2,
    "altro": 1,
}


def _chunk_content(content: str, max_words: int) -> list[str]:
    """
    Split *content* into chunks of at most *max_words* words each.
    Returns a list with at least one element (the original content if short enough).
    """
    words = content.split()
    if len(words) <= max_words:
        return [content]
    return [
        " ".join(words[i: i + max_words])
        for i in range(0, len(words), max_words)
    ]


def _merge_chunk_results(chunks: list[Analisi_Paragrafo]) -> Analisi_Paragrafo:
    """
    Merge multiple Analisi_Paragrafo results (from chunked LLM calls) into one.

    - pattern_type : highest priority value wins
    - requirement  : non-empty values joined with "; "
    - core_text    : non-empty values joined with " "
    - search_text  : non-empty values joined with " "
    - riferimenti  : union, deduplicated by (n_articolo, nome_documento)
    """
    if not chunks:
        return Analisi_Paragrafo()
    if len(chunks) == 1:
        return chunks[0]

    best = max(
        chunks,
        key=lambda c: _PATTERN_PRIORITY.get(
            c.pattern_type.value if c.pattern_type else "altro", 1
        ),
    )

    requirements = [c.requirement for c in chunks if c.requirement]
    core_texts   = [c.core_text   for c in chunks if c.core_text]
    search_texts = [c.search_text for c in chunks if c.search_text]

    seen_refs: set[tuple] = set()
    merged_refs = []
    for chunk in chunks:
        for ref in chunk.riferimenti or []:
            key = (ref.n_articolo, ref.nome_documento)
            if key not in seen_refs:
                seen_refs.add(key)
                merged_refs.append(ref)

    return Analisi_Paragrafo(
        requirement="; ".join(requirements) if requirements else None,
        core_text=" ".join(core_texts) if core_texts else None,
        search_text=" ".join(search_texts) if search_texts else None,
        pattern_type=best.pattern_type,
        riferimenti=merged_refs if merged_refs else None,
    )



async def analisi_parallel(
    articoli,
    document_name="nome documento analizzato",
    output_file_path_str: str | None = None,
):
    # list of articles with parsed content

    logger.info("Starting parallel analysis 🔎")

    # Ensure document_name is just the stem if it looks like a full filename
    temp_name = Path(document_name).name
    if temp_name.lower().endswith(".pdf"):
        doc_name_stem = temp_name.replace(".pdf", "")
    else:
        doc_name_stem = Path(temp_name).stem

    # get static prompts
    [system_prompt, user_prompt] = [
        load_prompt("system.txt"),
        load_prompt("analisi.txt"),
    ]

    # Minimum content length to send to LLM (skip empty/trivial items)
    MIN_CONTENT_LEN = 10
    # Maximum words per request to avoid RunPod 120s timeout
    # MAX_CONTENT_WORDS = 400  # kept for reference — now handled by chunking

    # flat_inputs  : one entry per chunk (multiple chunks per long comma)
    # comma_indices: maps each flat_input entry back to its comma index in
    #                merged_results, so chunks can be merged after LLM calls
    flat_inputs:   list = []
    comma_indices: list[int] = []
    flat_chunk_contents: list[str] = []
    debug_log = []
    article_lengths = []
    comma_idx = 0  # increments once per comma, regardless of chunks

    logger.info(f"[# ARTICOLI]: {len(articoli)}")

    # Almeno un Comma per Articolo e un Sottocomma per Comma (identificativo "0" se non suddivisibile)
    normalizza_gerarchia_articoli(articoli)

    for art in articoli:
        debug_log.append("#################################   COMMA   ######################################################")
        article_lengths.append(len(art["contenuto_parsato"]))
        for comma in art["contenuto_parsato"]:
            if len(comma.get("contenuto_parsato_2", [])) > 0:
                for sottocomma in comma.get("contenuto_parsato_2", []):
                    content = sottocomma.get("contenuto", "").strip()
                    if not content_ok_for_llm(
                        content, sottocomma.get("identificativo"), MIN_CONTENT_LEN
                    ):
                        logger.debug(
                            "Skipping empty sottocomma: id=%s",
                            sottocomma.get("identificativo", ""),
                        )
                        continue
                    # words = content.split()
                    # if len(words) > MAX_CONTENT_WORDS:
                    #     logger.warning("Truncating sottocomma %s from %d to %d words", sottocomma.get("identificativo", ""), len(words), MAX_CONTENT_WORDS)
                    #     content = " ".join(words[:MAX_CONTENT_WORDS])
                    chunks = _chunk_content(content, MAX_CHUNK_WORDS)
                    if len(chunks) > 1:
                        logger.info(
                            "Sottocomma %s split into %d chunks (%d words total)",
                            sottocomma.get("identificativo", ""),
                            len(chunks),
                            len(content.split()),
                        )
                    debug_log.append(f"          --> Sottocomma {sottocomma.get('identificativo', '')} di articolo {art['titolo']}  - contenuto lungo {len(content)} - chunks: {len(chunks)}")
                    for chunk in chunks:
                        flat_inputs.append(
                            [
                                SystemMessage(content=system_prompt),
                                HumanMessage(
                                    content=f"{user_prompt} titolo articolo cui appartiene il comma:'{art['titolo']}';"
                                    + f"contenuto del sottocomma: '{chunk}';"
                                    + f"identificativo del sottocomma: '{sottocomma.get('identificativo', '')}';"
                                    + f"flag del sottocomma: '{sottocomma.get('flag', '')}'"
                                ),
                            ]
                        )
                        comma_indices.append(comma_idx)
                        flat_chunk_contents.append(chunk)
                    comma_idx += 1
            else:
                content = comma.get("contenuto", "").strip()
                if not content_ok_for_llm(
                    content, comma.get("identificativo"), MIN_CONTENT_LEN
                ):
                    logger.debug(
                        "Skipping empty comma: id=%s", comma.get("identificativo", "")
                    )
                    continue
                # words = content.split()
                # if len(words) > MAX_CONTENT_WORDS:
                #     logger.warning("Truncating comma %s from %d to %d words", comma.get("identificativo", ""), len(words), MAX_CONTENT_WORDS)
                #     content = " ".join(words[:MAX_CONTENT_WORDS])
                chunks = _chunk_content(content, MAX_CHUNK_WORDS)
                if len(chunks) > 1:
                    logger.info(
                        "Comma %s split into %d chunks (%d words total)",
                        comma.get("identificativo", ""),
                        len(chunks),
                        len(content.split()),
                    )
                debug_log.append(f"          --> Comma {comma.get('identificativo', '')} di articolo {art['titolo']}  - contenuto lungo {len(content)} - chunks: {len(chunks)}")
                for chunk in chunks:
                    flat_inputs.append(
                        [
                            SystemMessage(content=system_prompt),
                            HumanMessage(
                                content=f"{user_prompt} titolo articolo cui appartiene il comma:'{art['titolo']}';"
                                + f"contenuto del comma: '{chunk}';"
                                + f"identificativo del comma: '{comma.get('identificativo', '')}';"
                                + f"flag del comma: 'False'"
                            ),
                        ]
                    )
                    comma_indices.append(comma_idx)
                    flat_chunk_contents.append(chunk)
                comma_idx += 1

    total_chunks = len(flat_inputs)
    total_commas = comma_idx

    token_counts = [len(msgs[-1].content.split()) for msgs in flat_inputs]
    if token_counts:
        logger.info(
            "LLM batch stats — commas: %d, chunks: %d, tokens/chunk: min=%d avg=%d max=%d",
            total_commas,
            total_chunks,
            min(token_counts),
            sum(token_counts) // len(token_counts),
            max(token_counts),
        )

    print("     - Analisi_parallel 🥔🥔🥔", total_commas, f"({total_chunks} chunks)")

    _pre_filter = len(flat_inputs)
    _keep = [i for i, c in enumerate(flat_chunk_contents) if len(c.strip()) >= 30]
    _skipped = _pre_filter - len(_keep)
    if _skipped:
        logger.info(
            "Skipping %d/%d trivial chunks (< 30 chars) before LLM; sending %d",
            _skipped, _pre_filter, len(_keep),
        )
    flat_inputs   = [flat_inputs[i]   for i in _keep]
    comma_indices = [comma_indices[i] for i in _keep]
    total_chunks  = len(flat_inputs)

    # 2️⃣  LLM calls — concurrent with semaphore --------------------------
    if total_chunks == 0:
        raw_results = []
    else:
        import asyncio as _asyncio

        llm          = _get_structured_llm()
        llm_fallback = _get_structured_llm_fallback()

        semaphore = _asyncio.Semaphore(25)  # max 25 concurrent RunPod calls
        call_times: list[float] = []

        async def _invoke_with_fallback(inp, i, llm, llm_fallback) -> Analisi_Paragrafo:
            async with semaphore:
                t_call = time.time()

                # Attempt 1: structured output with exponential backoff (15s, then 25s)
                for attempt, timeout_secs in enumerate([15, 60]):
                    try:
                        result = await _asyncio.wait_for(llm.ainvoke(inp), timeout=timeout_secs)
                        elapsed = time.time() - t_call
                        call_times.append(elapsed)
                        logger.debug("Chunk %d/%d completed in %.2fs", i + 1, total_chunks, elapsed)
                        if (i + 1) % 25 == 0 or (i + 1) == total_chunks:
                            logger.info("Progress: chunk %d/%d complete", i + 1, total_chunks)
                        return result
                    except _asyncio.TimeoutError:
                        logger.warning(
                            "Chunk %d/%d structured attempt %d/2 timed out after %ds",
                            i + 1, total_chunks, attempt + 1, timeout_secs,
                        )
                    except ContentFilterFinishReasonError:
                        logger.warning("Chunk %d/%d blocked by content filter — using default", i + 1, total_chunks)
                        elapsed = time.time() - t_call
                        call_times.append(elapsed)
                        return Analisi_Paragrafo(
                            requirement="[Content filter: comma non analizzabile]",
                            core_text="", search_text="", pattern_type="altro",
                        )
                    except Exception as e:
                        logger.warning(
                            "Chunk %d/%d structured attempt %d/2 failed (%s)",
                            i + 1, total_chunks, attempt + 1, e,
                        )
                        break  # Don't retry on non-timeout errors

                # Attempt 2: plain text prompt, 30s timeout on fallback model
                logger.warning(
                    "Chunk %d/%d structured output failed — trying plain text fallback",
                    i + 1, total_chunks,
                )
                try:
                    plain_prompt = [HumanMessage(content=(
                        "Analizza questo testo legale e rispondi SOLO con questo formato:\n"
                        "REQUIREMENT: [requisito principale in una frase]\n"
                        "CORE_TEXT: [testo centrale]\n"
                        "SEARCH_TEXT: [testo per ricerca]\n"
                        "PATTERN_TYPE: [tipo: obbligo/divieto/facoltà/definizione/altro]\n"
                        "RIFERIMENTI: [riferimenti normativi separati da virgola, o 'nessuno']\n\n"
                        f"Testo: {inp[-1].content}"
                    ))]
                    raw = await _asyncio.wait_for(llm_fallback.ainvoke(plain_prompt), timeout=30.0)
                    text = raw.content if hasattr(raw, "content") else str(raw)

                    def _extract_field(label: str, src: str) -> str:
                        for line in src.split("\n"):
                            if line.startswith(label + ":"):
                                return line[len(label) + 1:].strip()
                        return ""

                    elapsed = time.time() - t_call
                    call_times.append(elapsed)
                    logger.debug("Chunk %d/%d plain text fallback completed in %.2fs", i + 1, total_chunks, elapsed)
                    return Analisi_Paragrafo(
                        requirement=_extract_field("REQUIREMENT", text),
                        core_text=_extract_field("CORE_TEXT", text),
                        search_text=_extract_field("SEARCH_TEXT", text),
                        pattern_type=_extract_field("PATTERN_TYPE", text) or "altro",
                        riferimenti=None,
                    )
                except Exception as e2:
                    logger.warning(
                        "Chunk %d/%d plain text fallback failed (%s) — using default",
                        i + 1, total_chunks, e2,
                    )

                elapsed = time.time() - t_call
                call_times.append(elapsed)
                return Analisi_Paragrafo(
                    requirement="[Errore analisi comma]",
                    core_text="", search_text="", pattern_type="altro",
                )

        logger.info("[TIMING] total_chunks=%d, method=parallel_gather", total_chunks)
        logger.info("[TIMING] Starting parallel LLM analysis: %d chunks, semaphore=25", total_chunks)
        t_gather = time.time()
        raw_results = list(await _asyncio.gather(
            *[_invoke_with_fallback(inp, i, llm, llm_fallback) for i, inp in enumerate(flat_inputs)]
        ))
        if call_times:
            logger.info(
                "[TIMING] Parallel LLM analysis complete: %d chunks in %.1fs | call: min=%.1fs avg=%.1fs max=%.1fs",
                total_chunks,
                time.time() - t_gather,
                min(call_times), sum(call_times) / len(call_times), max(call_times),
            )
        else:
            logger.info("[TIMING] Parallel LLM analysis complete: no LLM calls made (all filtered)")

    # 3️⃣  Merge chunks → one result per comma ---------------------------
    chunks_by_comma: dict[int, list[Analisi_Paragrafo]] = defaultdict(list)
    for i, result in enumerate(raw_results):
        chunks_by_comma[comma_indices[i]].append(result)

    results: list[Analisi_Paragrafo] = [
        _merge_chunk_results(chunks_by_comma[idx])
        for idx in range(total_commas)
    ]

    chunked_count = sum(1 for idx in range(total_commas) if len(chunks_by_comma[idx]) > 1)
    if chunked_count:
        logger.info(
            "Chunk merging done — %d comma(s) were split and merged back", chunked_count
        )

    dump_mode = "json" if lex_package_is_installed() else None
    dicts: list[dict] = [
        x.model_dump(mode=dump_mode, exclude_none=True) for x in results
    ]

    # 4️⃣  Ricomposizione -----------------------------------------

    counter = 0
    for a in articoli:
        for c in a["contenuto_parsato"]:
            if len(c.get("contenuto_parsato_2", [])) > 0:
                debug_log.append("##################################################################################################")
                first_ref = None
                for j, sc in enumerate(c.get("contenuto_parsato_2", [])):
                    sc_content = sc.get("contenuto", "").strip()
                    if not content_ok_for_llm(
                        sc_content, sc.get("identificativo"), MIN_CONTENT_LEN
                    ):
                        continue
                    sc.update(dicts[counter])
                    emb_text = f"{a.get('titolo','')}\n{(sc.get('core_text') or sc.get('contenuto') or '').strip()}"
                    sc["embedding"] = embed_text(emb_text)
                    if first_ref is None:
                        first_ref = sc.get("riferimenti", [])
                    elif first_ref:
                        riferimenti_0 = [{**r, "ereditato": True} for r in first_ref]
                        sc.setdefault("riferimenti", []).extend(riferimenti_0)
                    debug_log.append(f"\n  sc aggiornato (indice {j}): {sc}\n")
                    counter += 1
            else:
                continue

    for a in articoli:
        for c in a["contenuto_parsato"]:
            if not c.get("contenuto_parsato_2", ""):
                content = c.get("contenuto", "").strip()
                if len(content) < MIN_CONTENT_LEN:
                    continue  # skip same empty items as in building phase
                nuovo = {
                    "contenuto": c.get("contenuto", ""),
                    "identificativo": "0",
                    "flag": c.get("flag", ""),
                    "riempito": True,
                }
                nuovo.update(dicts[counter])  # <-- AGGIUNGE RISPOSTA AI
                emb_text = f"{a.get('titolo','')}\n{(nuovo.get('core_text') or nuovo.get('contenuto') or '').strip()}"
                nuovo["embedding"] = embed_text(emb_text)
                c["contenuto_parsato_2"] = [nuovo]
                counter += 1

    # Sottocommi saltati dal batch LLM (testo troppo corto): embedding comunque dal contenuto
    for a in articoli:
        for c in a.get("contenuto_parsato", []) or []:
            for sc in c.get("contenuto_parsato_2", []) or []:
                if "embedding" in sc:
                    continue
                emb_text = f"{a.get('titolo','')}\n{(sc.get('core_text') or sc.get('contenuto') or '').strip()}"
                sc["embedding"] = embed_text(emb_text)

    for a in articoli:
        pieces: list[str] = []
        for c in a.get("contenuto_parsato", []) or []:
            for sc in c.get("contenuto_parsato_2", []) or []:
                t = (sc.get("core_text") or sc.get("contenuto") or "").strip()
                if t:
                    pieces.append(t)
        articolo_text = f"{a.get('titolo','')}\n" + "\n".join(pieces[:200])
        a["embedding"] = embed_text(articolo_text)

    try:
        from utils.blob_storage_client import upload_debug_log
        upload_debug_log("debug_log_AnalisiParallel.txt", "\n".join(debug_log))
    except Exception:
        pass

    logger.info("Finished parallel analysis 🔎")
    return articoli
