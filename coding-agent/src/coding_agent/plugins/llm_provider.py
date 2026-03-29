"""LLMProviderPlugin — provides LLM backend via provide_llm hook."""

from __future__ import annotations

from typing import Any, Callable

from agentkit.providers.protocol import LLMProvider


class LLMProviderPlugin:
    state_key = "llm_provider"

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        self._provider_name = provider
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._instance: LLMProvider | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"provide_llm": self.provide_llm}

    def provide_llm(self, **kwargs: Any) -> LLMProvider:
        if self._instance is not None:
            return self._instance

        if self._provider_name == "anthropic":
            from coding_agent.providers.anthropic import AnthropicProvider

            self._instance = AnthropicProvider(
                model=self._model,
                api_key=self._api_key,
            )
        elif self._provider_name in ("openai", "openai_compat"):
            from coding_agent.providers.openai_compat import OpenAICompatProvider

            self._instance = OpenAICompatProvider(
                model=self._model,
                api_key=self._api_key,
                base_url=self._base_url,
            )
        else:
            raise ValueError(f"unsupported provider: {self._provider_name}")

        return self._instance
