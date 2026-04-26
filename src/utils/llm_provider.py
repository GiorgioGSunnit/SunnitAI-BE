"""
Async LLM provider — uses standard OpenAI-compatible client.

Replaces the Azure OpenAI / APIM version with a plain openai.AsyncOpenAI
client that works with any OpenAI-compatible endpoint (RunPod, vLLM, Ollama).

Required env vars:
    LLM_BASE_URL  — e.g. http://server:8000/v1
    LLM_API_KEY   — API key ("EMPTY" works for local/unauthenticated servers)
    LLM_MODEL     — model name / deployment

Optional fallback via AZURE_OPENAI_* names for backward compatibility.
"""
import os
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI


def _get(primary: str, *fallbacks: str, default: str = "") -> str:
    for key in (primary, *fallbacks):
        val = os.getenv(key)
        if val:
            return val
    return default


class LLMProvider:
    """Async wrapper for an OpenAI-compatible chat completion API."""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str],
        deployment: str,
    ):
        self.deployment = deployment

        client_kwargs: Dict[str, Any] = {"api_key": api_key or "EMPTY"}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = AsyncOpenAI(**client_kwargs)
        logger.info(
            "LLMProvider: base_url={} model={}",
            base_url or "(default OpenAI)",
            deployment,
        )

    @classmethod
    def from_settings(cls) -> "LLMProvider":
        base_url = _get("LLM_BASE_URL")
        api_key = _get("LLM_API_KEY", "OPENAI_API_KEY", default="EMPTY")
        deployment = _get("LLM_MODEL", default="nemotron-2-30B-A3B")
        return cls(api_key=api_key, base_url=base_url or None, deployment=deployment)

    async def generate_response(self, messages: List[Dict[str, str]], **kwargs):
        try:
            return await self.client.chat.completions.create(
                model=self.deployment, messages=messages, **kwargs
            )
        except Exception as e:
            logger.error("LLMProvider.generate_response error: {}", e)
            raise

    async def generate_response_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        **kwargs,
    ):
        try:
            return await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs,
            )
        except Exception as e:
            logger.error("LLMProvider.generate_response_with_tools error: {}", e)
            raise

    async def stream_response(self, messages: List[Dict[str, str]], **kwargs):
        try:
            stream = await self.client.chat.completions.create(
                model=self.deployment, messages=messages, stream=True, **kwargs
            )
            async for chunk in stream:
                yield chunk
        except Exception as e:
            logger.error("LLMProvider.stream_response error: {}", e)
            raise

    async def stream_response_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        **kwargs,
    ):
        try:
            stream = await self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=True,
                **kwargs,
            )
            async for chunk in stream:
                yield chunk
        except Exception as e:
            logger.error("LLMProvider.stream_response_with_tools error: {}", e)
            raise


def get_llm_provider() -> LLMProvider:
    """Restituisce un'istanza del provider (lazy loading)."""
    return LLMProvider.from_settings()
