from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional


@lru_cache(maxsize=1)
def _features() -> dict:
    """
    Feature flags loaded from `lex_package/config/features.json`.
    """
    p = Path(__file__).resolve().parents[1] / "config" / "features.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # Safe default: keep embeddings disabled unless explicitly enabled.
        return {"embeddings_enabled": False}


def embeddings_enabled() -> bool:
    return bool((_features() or {}).get("embeddings_enabled", False))


@lru_cache(maxsize=1)
def _get_embedder():
    if not embeddings_enabled():
        raise RuntimeError("Embeddings are disabled by configuration.")
    # Uses the OpenAI-compatible embeddings endpoint configured via
    # LLM_EMBEDDING_BASE_URL / LLM_EMBEDDING_API_KEY / LLM_EMBEDDING_MODEL.
    try:
        from lex_package.llm.factory import build_embedding_model
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "Embeddings dependencies are missing. Ensure 'langchain-openai' is installed "
            "and LLM_EMBEDDING_MODEL is set in your .env."
        ) from e
    return build_embedding_model(target="primary")


def embed_text(text: str) -> list[float]:
    """Calcola l'embedding per *text*.

    Se gli embedding non sono abilitati, la configurazione manca o la chiamata
    al provider fallisce (rete, auth, quota, ecc.), restituisce ``[]`` senza
    propagare eccezioni, così analisi e flatten non si interrompono.
    """
    t = (text or "").strip()
    if not t:
        return []
    if not embeddings_enabled():
        return []
    try:
        vec = _get_embedder().embed_query(t)
        return [float(x) for x in vec]
    except Exception:
        return []


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    a_list = list(a)
    b_list = list(b)
    if not a_list or not b_list:
        return 0.0
    n = min(len(a_list), len(b_list))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a_list[i])
        y = float(b_list[i])
        dot += x * y
        na += x * x
        nb += y * y
    denom = (math.sqrt(na) * math.sqrt(nb)) + 1e-9
    return float(dot / denom)


def embedding_dim(vec: Optional[Iterable[float]]) -> int:
    if not vec:
        return 0
    try:
        return len(list(vec))
    except Exception:
        return 0


def embedding_to_xlsx_string(vec: Optional[Iterable[float]], *, head: int = 64) -> str:
    """Compact embedding string for Excel (avoids 32k cell limit)."""
    if not vec:
        return ""
    v = list(vec)
    head_v = v[: max(0, int(head))]
    s = json.dumps([round(float(x), 6) for x in head_v], ensure_ascii=False)
    if len(v) > head:
        s = s[:-1] + f', "...(+{len(v)-head} dims)"]'
    return s

