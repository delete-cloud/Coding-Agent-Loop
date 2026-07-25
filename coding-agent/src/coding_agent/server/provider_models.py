"""Live model listing for providers, used by the HTTP server."""

from __future__ import annotations

import asyncio
import logging

from coding_agent.core.config import ProviderName
from coding_agent.plugins.llm_provider import LLMProviderPlugin
from coding_agent.providers.openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)

# Short timeout so a dead provider cannot hang the webui.
LIST_MODELS_TIMEOUT_SECONDS = 10.0


async def list_provider_models(
    provider: ProviderName,
    *,
    timeout: float = LIST_MODELS_TIMEOUT_SECONDS,
) -> list[str]:
    """Return model ids for an OpenAI-compatible provider.

    Provider construction reuses ``LLMProviderPlugin.provide_llm`` so base_url
    and env-var API-key resolution stay in one place. A fresh plugin instance
    is created per call, so the constructed provider (and its httpx client) is
    short-lived and is closed here — the plugin-level instance cache is never
    shared across requests.

    Returns an empty list when the provider is not OpenAI-compatible
    (anthropic, codex, kimi-code-anthropic, copilot). Raises on construction
    or network failure; callers decide how to surface that.
    """
    plugin = LLMProviderPlugin(provider=provider, model="", api_key="")
    instance = plugin.provide_llm()
    if not isinstance(instance, OpenAICompatProvider):
        return []
    try:
        return await asyncio.wait_for(instance.list_models(), timeout=timeout)
    finally:
        await instance.close()
