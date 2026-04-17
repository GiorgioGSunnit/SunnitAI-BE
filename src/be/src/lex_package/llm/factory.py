"""
LLM factory — builds chat and embedding models from environment variables.

Supports any OpenAI-compatible endpoint (RunPod, vLLM, Ollama, etc.) via:
    LLM_BASE_URL   — e.g. http://server:8000/v1
    LLM_API_KEY    — API key (can be "EMPTY" for local servers)
    LLM_MODEL      — model name / deployment

Primary and fallback targets read from separate env var sets:
    Primary:  LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
    Fallback: LLM_BASE_URL_FALLBACK / LLM_API_KEY_FALLBACK / LLM_FALLBACK_MODEL

For embeddings:
    LLM_EMBEDDING_BASE_URL  — endpoint (defaults to LLM_BASE_URL)
    LLM_EMBEDDING_API_KEY   — key (defaults to LLM_API_KEY)
    LLM_EMBEDDING_MODEL     — embedding model name
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from langchain_openai import ChatOpenAI, OpenAIEmbeddings


# ── Env-var lookup tables ─────────────────────────────────────────────────────

_MODEL_ENV: Dict[str, Tuple[str, ...]] = {
    "primary": ("LLM_MODEL", "AZURE_OPENAI_DEPLOYMENT_NAME"),
    "fallback": ("LLM_FALLBACK_MODEL", "LLM_MODEL", "AZURE_OPENAI_DEPLOYMENT_NAME_BIS"),
}

_API_KEY_ENV: Dict[str, Tuple[str, ...]] = {
    "primary": ("LLM_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"),
    "fallback": (
        "LLM_API_KEY_FALLBACK",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
    ),
}

_BASE_URL_ENV: Dict[str, Tuple[str, ...]] = {
    "primary": ("LLM_BASE_URL", "AZURE_OPENAI_ENDPOINT"),
    "fallback": ("LLM_BASE_URL_FALLBACK", "LLM_BASE_URL", "AZURE_OPENAI_ENDPOINT"),
}

_DEFAULT_MODELS: Dict[str, str] = {
    "primary": "gpt-4o-mini",
    "fallback": "gpt-4o-mini",
}


def _env(*keys: str, default: Optional[str] = None) -> Optional[str]:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


@dataclass(frozen=True)
class ProviderConfig:
    model_name: str
    api_key: str
    base_url: Optional[str]


def _resolve_config(target: str) -> ProviderConfig:
    if target not in _MODEL_ENV:
        raise ValueError(f"Unsupported LLM target '{target}'. Use 'primary' or 'fallback'.")

    model_name = _env(*_MODEL_ENV[target], default=_DEFAULT_MODELS[target])
    api_key = _env(*_API_KEY_ENV[target], default="EMPTY")  # "EMPTY" works with local servers
    base_url = _env(*_BASE_URL_ENV[target])

    return ProviderConfig(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
    )


def build_chat_model(
    *,
    target: str = "primary",
    temperature: float = 0.0,
    overrides: Dict[str, Any] | None = None,
) -> ChatOpenAI:
    """Return a ChatOpenAI model configured from environment variables.

    Args:
        target: ``"primary"`` or ``"fallback"``.
        temperature: Model temperature.
        overrides: Optional explicit overrides (model_name, api_key, base_url).
    """
    overrides = overrides or {}
    cfg = _resolve_config(target)

    model_name = overrides.get("model_name", cfg.model_name)
    api_key = overrides.get("api_key", cfg.api_key)
    base_url = overrides.get("base_url", cfg.base_url)

    kwargs: Dict[str, Any] = {
        "model": model_name,
        "api_key": api_key,
        "temperature": temperature,
    }
    if base_url:
        kwargs["base_url"] = base_url

    return ChatOpenAI(**kwargs)


def build_embedding_model(
    *,
    target: str = "primary",
    overrides: Dict[str, Any] | None = None,
) -> OpenAIEmbeddings:
    """Return an OpenAIEmbeddings model configured from environment variables.

    Embedding-specific env vars take priority; falls back to LLM vars:
        LLM_EMBEDDING_BASE_URL  → LLM_BASE_URL
        LLM_EMBEDDING_API_KEY   → LLM_API_KEY
        LLM_EMBEDDING_MODEL     (required — do NOT fall back to chat model name)
    """
    overrides = overrides or {}

    embedding_model = (
        overrides.get("model")
        or _env("LLM_EMBEDDING_MODEL", "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME")
    )
    if not embedding_model:
        raise ValueError(
            "Embedding model name is required. Set LLM_EMBEDDING_MODEL in .env."
        )

    api_key = (
        overrides.get("api_key")
        or _env(
            "LLM_EMBEDDING_API_KEY",
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "AZURE_OPENAI_API_KEY",
            default="EMPTY",
        )
    )

    base_url = overrides.get("base_url") or _env(
        "LLM_EMBEDDING_BASE_URL", "LLM_BASE_URL", "AZURE_OPENAI_ENDPOINT"
    )

    kwargs: Dict[str, Any] = {
        "model": embedding_model,
        "api_key": api_key,
    }
    if base_url:
        kwargs["base_url"] = base_url

    return OpenAIEmbeddings(**kwargs)
