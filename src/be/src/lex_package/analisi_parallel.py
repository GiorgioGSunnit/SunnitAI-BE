import logging
import time
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
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
    """Return the LLM wrapped with the proper structured output + retry policy."""
    return llm_base.with_structured_output(Analisi_Paragrafo).with_retry(
        retry_if_exception_type=(RateLimitError, APITimeoutError, InternalServerError),
        stop_after_attempt=5,
        wait_exponential_jitter=True,
    )


@lru_cache(maxsize=1)
def _get_structured_llm():
    """Lazy-init dello structured LLM primario."""
    return _build_structured(_get_llm())


@lru_cache(maxsize=1)
def _get_structured_llm_fallback():
    """Lazy-init dello structured LLM fallback."""
    return _build_structured(_get_llm_fallback())


MAX_CONCURRENCY = 1

# ---------------------------------------------------------------------------
# Helper that first tries primary LLM; if a 429 with a Retry-After >1s occurs
# automatically retries the whole batch on the fallback deployment. Mirrors the
# implementation used in versioning_confronto.py so behaviour is consistent.
# ---------------------------------------------------------------------------


async def _invoke_with_fallback_batch(primary_llm, fallback_llm, batches, cfg):
    """Invoke **batches** with *primary_llm*.

    If a RateLimitError is raised and its HTTP response has a Retry-After header
    greater than one second, re-issue the same call to *fallback_llm* instead of
    waiting, providing an automatic high-availability path.
    """
    try:
        return await primary_llm.abatch(batches, config=cfg)
    except RateLimitError as e:
        delay = None
        if getattr(e, "response", None):
            try:
                delay = float(e.response.headers.get("Retry-After"))
            except (TypeError, ValueError):
                pass

        if delay and delay > 1:
            logger.warning(f"[Retry-After={delay}s] – switching to fallback deployment")
            return await fallback_llm.abatch(batches, config=cfg)

        raise  # propagate so the built-in retry policy can kick in


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

    # Flatten every comma into one big list
    flat_inputs = []
    debug_log = []
    article_lengths = []

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
                    debug_log.append(f"          --> Sottocomma {sottocomma.get('identificativo', '')} di articolo {art['titolo']}  - contenuto lungo {len(content)}")
                    flat_inputs.append(
                        [
                            SystemMessage(content=system_prompt),
                            HumanMessage(
                                content=f"{user_prompt} titolo articolo cui appartiene il comma:'{art['titolo']}';"
                                + f"contenuto del sottocomma: '{content}';"
                                + f"identificativo del sottocomma: '{sottocomma.get('identificativo', '')}';"
                                + f"flag del sottocomma: '{sottocomma.get('flag', '')}'"
                            ),
                        ]
                    )
            else:
                content = comma.get("contenuto", "").strip()
                if not content_ok_for_llm(
                    content, comma.get("identificativo"), MIN_CONTENT_LEN
                ):
                    logger.debug(
                        "Skipping empty comma: id=%s", comma.get("identificativo", "")
                    )
                    continue
                debug_log.append(f"          --> Comma {comma.get('identificativo', '')} di articolo {art['titolo']}  - contenuto lungo {len(content)}")
                flat_inputs.append(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(
                            content=f"{user_prompt} titolo articolo cui appartiene il comma:'{art['titolo']}';"
                            + f"contenuto del comma: '{content}';"
                            + f"identificativo del comma: '{comma.get('identificativo', '')}';"
                            + f"flag del comma: 'False'"
                        ),
                    ]
                )

    total_items = len(flat_inputs)

    token_counts = [len(msgs[-1].content.split()) for msgs in flat_inputs]
    if token_counts:
        logger.info(
            "LLM batch stats — requests: %d, tokens/req: min=%d avg=%d max=%d, concurrency: %d",
            total_items,
            min(token_counts),
            sum(token_counts) // len(token_counts),
            max(token_counts),
            MAX_CONCURRENCY,
        )

    print("     - Analisi_parallel 🥔🥔🥔", total_items)

    # 2️⃣  Parallel call with automatic fallback -----------------
    cfg = RunnableConfig(max_concurrency=MAX_CONCURRENCY)
    if total_items == 0:
        results = []
    else:
        try:
            t0 = time.time()
            results = await _invoke_with_fallback_batch(
                _get_structured_llm(),
                _get_structured_llm_fallback(),
                flat_inputs,
                cfg,
            )
            elapsed = time.time() - t0
            logger.info(
                "LLM batch completed — %d requests in %.1fs (avg %.1fs/req)",
                total_items, elapsed, elapsed / total_items,
            )
        except ContentFilterFinishReasonError:
            logger.warning(
                "Content filter hit on batch – falling back to one-by-one processing"
            )
            llm = _get_structured_llm()
            results = []
            for i, inp in enumerate(flat_inputs):
                try:
                    r = await llm.ainvoke(inp)
                    results.append(r)
                except ContentFilterFinishReasonError:
                    logger.warning(f"Content filter blocked item {i}, using default")
                    results.append(
                        Analisi_Paragrafo(
                            riferimento_articolo="",
                            requirement="[Content filter: comma non analizzabile]",
                            core_text="",
                            search_text="",
                            pattern_type="altro",
                        )
                    )
                except Exception as e:
                    logger.warning(f"Item {i} failed ({e}), using default")
                    results.append(
                        Analisi_Paragrafo(
                            riferimento_articolo="",
                            requirement="[Errore analisi comma]",
                            core_text="",
                            search_text="",
                            pattern_type="altro",
                        )
                    )

    dump_mode = "json" if lex_package_is_installed() else None
    dicts: list[dict] = [
        x.model_dump(mode=dump_mode, exclude_none=True) for x in results
    ]
    # 3️⃣  Ricomposizione -----------------------------------------

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

    from utils.blob_storage_client import upload_debug_log
    upload_debug_log("debug_log_AnalisiParallel.txt", "\n".join(debug_log))

    logger.info("Finished parallel analysis 🔎")
    return articoli
