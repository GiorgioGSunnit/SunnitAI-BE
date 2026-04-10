from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from langchain_openai import AzureChatOpenAI
from langchain_openai import AzureOpenAIEmbeddings
from langchain.chat_models import init_chat_model
from azure.identity import ClientSecretCredential, get_bearer_token_provider

from core.settings import get_settings

# Factory per configurare i worker LLM da un unico posto.
# Usa azure_ad_token_provider per autenticazione APIM con token v2.

_MODEL_ENV = {
    "primary": ("LLM_MODEL", "AZURE_OPENAI_DEPLOYMENT_NAME"),
    "fallback": ("LLM_FALLBACK_MODEL", "AZURE_OPENAI_DEPLOYMENT_NAME_BIS"),
}

_PROVIDER_ENV = {
    "primary": ("LLM_PROVIDER",),
    "fallback": ("LLM_PROVIDER_FALLBACK", "LLM_PROVIDER"),
}

_AZURE_DEPLOYMENT_ENV = {
    "primary": ("LLM_AZURE_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME"),
    "fallback": ("LLM_AZURE_DEPLOYMENT_FALLBACK", "AZURE_OPENAI_DEPLOYMENT_NAME_BIS"),
}

_API_VERSION_ENV = {
    "primary": ("LLM_API_VERSION", "AZURE_OPENAI_API_VERSION"),
    "fallback": (
        "LLM_API_VERSION_FALLBACK",
        "AZURE_OPENAI_API_VERSION_BIS",
        "AZURE_OPENAI_API_VERSION",
    ),
}

_BASE_URL_ENV = {
    "primary": ("LLM_BASE_URL",),
    "fallback": ("LLM_BASE_URL_FALLBACK", "LLM_BASE_URL"),
}

_DEFAULT_MODELS = {"primary": "gpt-4o-mini", "fallback": "gpt-4o-mini-bis"}

# Scope per token v2 (App Registration ID)
_DEFAULT_SCOPE = "c989d43a-9e62-4c01-a67c-7eefbeef70ce/.default"

# Cache per token provider e credential (singleton)
_token_provider = None
_sp_credential = None


def _get_service_principal_credential():
    """Restituisce ClientSecretCredential per il service principal Reader."""
    global _sp_credential
    if _sp_credential is None:
        tenant_id = os.getenv("AZURE_TENANT_ID")
        client_id = os.getenv("AZURE_READER__CLIENT_ID")
        client_secret = os.getenv("AZURE_READER__CLIENT_SECRET")
        if not all([tenant_id, client_id, client_secret]):
            raise ValueError(
                "Service principal credentials not found. "
                "Ensure AZURE_TENANT_ID, AZURE_READER__CLIENT_ID, AZURE_READER__CLIENT_SECRET are set."
            )
        _sp_credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    return _sp_credential


def _get_token_provider():
    """Restituisce token provider per autenticazione APIM con token v2."""
    global _token_provider
    if _token_provider is None:
        # Usa ClientSecretCredential (service principal) per ottenere token v2
        cred = _get_service_principal_credential()
        # Usa scope da settings se disponibile, altrimenti default
        s = get_settings()
        scope = s.azure_openai.scope if s.azure_openai else _DEFAULT_SCOPE
        _token_provider = get_bearer_token_provider(cred, scope)
    return _token_provider


def _get_azure_config():
    """Restituisce configurazione Azure OpenAI da settings o env vars."""
    s = get_settings()
    if s.azure_openai:
        return {
            "endpoint": s.azure_openai.endpoint,
            "api_key": s.azure_openai.api_key,
            "api_version": s.azure_openai.api_version,
        }
    # Fallback: usa env vars (settate da bootstrap)
    return {
        "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
        "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
        "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
    }


def _env(keys: Tuple[str, ...], default: str | None = None) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model_name: str
    azure_deployment: str | None
    api_version: str | None
    base_url: str | None


def _resolve_config(target: str) -> ProviderConfig:
    if target not in _MODEL_ENV:
        raise ValueError(f"Unsupported LLM target '{target}'")

    provider = _env(_PROVIDER_ENV[target], "azure_openai")
    model_name = _env(_MODEL_ENV[target], _DEFAULT_MODELS[target])
    azure_deployment = _env(_AZURE_DEPLOYMENT_ENV[target], model_name)
    api_version = _env(_API_VERSION_ENV[target])
    base_url = _env(_BASE_URL_ENV[target])
    return ProviderConfig(
        provider=provider or "azure_openai",
        model_name=model_name,
        azure_deployment=azure_deployment,
        api_version=api_version,
        base_url=base_url,
    )


def build_chat_model(
    *,
    target: str = "primary",
    temperature: float = 0.0,
    overrides: Dict[str, Any] | None = None,
):
    """Return a chat model configured from environment variables.

    Args:
        target: Either ``"primary"`` or ``"fallback"``. Determines which set of
            environment variables are read.
        temperature: Temperature passed to the model.
        overrides: Optional explicit overrides for provider-specific kwargs.
    """
    overrides = overrides or {}
    cfg = _resolve_config(target)
    provider = overrides.get("provider", cfg.provider)

    if provider == "azure_openai":
        # Usa AzureChatOpenAI con token provider per APIM
        azure_deployment = overrides.get("azure_deployment", cfg.azure_deployment or cfg.model_name)
        if not azure_deployment:
            raise ValueError(
                "Azure deployment name is required when using provider 'azure_openai'"
            )
        az_cfg = _get_azure_config()
        api_version = overrides.get("api_version", cfg.api_version) or az_cfg["api_version"]
        
        return AzureChatOpenAI(
            azure_deployment=azure_deployment,
            api_version=api_version,
            azure_endpoint=az_cfg["endpoint"],
            azure_ad_token_provider=_get_token_provider(),
            default_headers={
                "api-key": az_cfg["api_key"],
                "Ocp-Apim-Subscription-Key": az_cfg["api_key"],
            },
            temperature=temperature,
        )
    else:
        # Fallback per altri provider (OpenAI, etc.)
        model_name = overrides.get("model_name", cfg.model_name)
        kwargs: Dict[str, Any] = {
            "model_provider": provider,
            "temperature": temperature,
        }
        base_url = overrides.get("base_url", cfg.base_url)
        if base_url:
            kwargs["base_url"] = base_url
        return init_chat_model(model_name, **kwargs)


def build_embedding_model(
    *,
    target: str = "primary",
    overrides: Dict[str, Any] | None = None,
):
    """Return an embeddings model configured from settings/env vars.

    Notes:
    - Uses the same Azure endpoint, API version and APIM token provider as chat.
    - Deployment name is resolved from Settings.azure_openai.embedding_model_deployment
      when available, otherwise falls back to env vars.
    """
    overrides = overrides or {}
    cfg = _resolve_config(target)
    provider = overrides.get("provider", cfg.provider)
    if provider != "azure_openai":
        raise ValueError("Only 'azure_openai' provider is supported for embeddings")

    s = get_settings()
    embedding_deployment = None
    if getattr(s, "azure_openai", None):
        embedding_deployment = getattr(s.azure_openai, "embedding_model_deployment", None)

    # Env fallbacks (keep names explicit to avoid accidental chat deployment reuse)
    embedding_deployment = (
        overrides.get("azure_deployment")
        or embedding_deployment
        or os.getenv("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME")
        or os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
        or os.getenv("LLM_AZURE_EMBEDDINGS_DEPLOYMENT")
    )
    if not embedding_deployment:
        # Do NOT fall back to chat deployment: many chat models do not support embeddings.
        raise ValueError(
            "Azure embeddings deployment name is required. "
            "Set Settings.azure_openai.embedding_model_deployment (KeyVault/env) or "
            "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME."
        )

    # Guardrail: avoid using a chat model deployment for embeddings.
    if str(embedding_deployment).lower().startswith("gpt-"):
        raise ValueError(
            "Embeddings deployment appears to be a chat model. "
            "Set AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT_NAME to a text-embedding deployment."
        )

    az_cfg = _get_azure_config()
    api_version = overrides.get("api_version", cfg.api_version) or az_cfg["api_version"]

    return AzureOpenAIEmbeddings(
        azure_deployment=embedding_deployment,
        api_version=api_version,
        azure_endpoint=az_cfg["endpoint"],
        azure_ad_token_provider=_get_token_provider(),
        default_headers={
            "api-key": az_cfg["api_key"],
            "Ocp-Apim-Subscription-Key": az_cfg["api_key"],
        },
    )
