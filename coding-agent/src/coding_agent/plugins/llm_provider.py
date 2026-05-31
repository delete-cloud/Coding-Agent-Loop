"""LLMProviderPlugin — provides LLM backend via provide_llm hook."""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from agentkit.providers.protocol import LLMProvider
from agentkit.providers.models import (
    TextEvent,
    ToolCallEvent,
    DoneEvent,
    StreamEvent as NewStreamEvent,
)
from coding_agent.providers.base import StreamEvent as OldStreamEvent


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
        elif self._provider_name == "copilot":
            from coding_agent.providers.copilot import CopilotProvider

            self._instance = CopilotProvider(
                model=self._model,
                api_key=self._api_key,
                base_url=self._base_url,
            )
        elif self._provider_name == "kimi":
            import os

            from coding_agent.providers.openai_compat import OpenAICompatProvider

            api_key = self._api_key or os.environ.get("MOONSHOT_API_KEY", "")
            self._instance = OpenAICompatProvider(
                model=self._model,
                api_key=api_key,
                base_url="https://api.moonshot.cn/v1",
            )
        elif self._provider_name == "kimi-code":
            import os

            from coding_agent.providers.openai_compat import OpenAICompatProvider

            api_key = self._api_key or os.environ.get("KIMI_CODE_API_KEY", "")
            self._instance = OpenAICompatProvider(
                model=self._model,
                api_key=api_key,
                base_url="https://api.kimi.com/coding/v1",
                default_headers={"User-Agent": "claude-code/1.0.17"},
            )
        elif self._provider_name == "kimi-code-anthropic":
            import os

            from coding_agent.providers.anthropic import AnthropicProvider

            api_key = self._api_key or os.environ.get("KIMI_CODE_API_KEY", "")
            self._instance = AnthropicProvider(
                model=self._model,
                api_key=api_key,
                base_url="https://api.kimi.com/coding/",
                default_headers={"User-Agent": "claude-code/1.0.17"},
            )
        elif self._provider_name == "deepseek":
            import os

            from coding_agent.providers.openai_compat import OpenAICompatProvider

            api_key = self._api_key or os.environ.get("DEEPSEEK_API_KEY", "")
            self._instance = OpenAICompatProvider(
                model=self._model,
                api_key=api_key,
                base_url=self._base_url or "https://api.deepseek.com",
            )
        elif self._provider_name == "stepfun":
            import os

            from coding_agent.providers.openai_compat import OpenAICompatProvider

            api_key = self._api_key or os.environ.get("STEP_API_KEY", "")
            self._instance = OpenAICompatProvider(
                model=self._model,
                api_key=api_key,
                base_url=self._base_url or "https://api.stepfun.com/v1",
            )
        elif self._provider_name == "codex":
            from coding_agent.oauth.auth import OAuthBearerAuth
            from coding_agent.oauth.codex import CODEX_BASE_URL
            from coding_agent.oauth.store import OAuthStore
            from coding_agent.providers.openai_compat import OpenAICompatProvider

            store = OAuthStore()
            record = store.get_provider("codex")
            if record is None:
                raise RuntimeError(
                    "Codex OAuth provider is not logged in. "
                    "Run `coding-agent oauth login codex` first."
                )

            token_source = StoreBackedCodexTokenSource(store)
            auth = OAuthBearerAuth(
                token_source,
                provider_name="codex",
            )
            base_url = self._base_url or CODEX_BASE_URL

            self._instance = OpenAICompatProvider(
                model=self._model,
                api_key="oauth-managed",
                base_url=base_url,
                httpx_auth=auth,
            )
        else:
            raise ValueError(f"unsupported provider: {self._provider_name}")

        return self._instance


class StoreBackedCodexTokenSource:
    """Small adapter that avoids constructing network clients until refresh."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def get_token(self) -> Any:
        from coding_agent.oauth.store import StoreBackedTokenSource

        return await StoreBackedTokenSource(
            "codex",
            store=self._store,
            refresh_provider=self._refresh_record,
        ).get_token()

    async def refresh_token(self) -> Any:
        from coding_agent.oauth.store import StoreBackedTokenSource

        return await StoreBackedTokenSource(
            "codex",
            store=self._store,
            refresh_provider=self._refresh_record,
        ).refresh_token()

    def _refresh_record(self, record: Any) -> Any:
        from coding_agent.oauth.codex import CodexOAuthClient

        client = CodexOAuthClient(store=self._store)
        try:
            return client.refresh_record(record)
        finally:
            client.close()


async def adapt_stream_events(
    old_stream: AsyncIterator[OldStreamEvent],
) -> AsyncIterator[NewStreamEvent]:
    """Adapt old StreamEvent types to new agentkit event types.

    Converts:
    - delta → TextEvent
    - tool_call → ToolCallEvent
    - done → DoneEvent
    - error → DoneEvent (error field not supported in agentkit)

    Args:
        old_stream: AsyncIterator yielding old StreamEvent objects

    Yields:
        New agentkit StreamEvent types (TextEvent, ToolCallEvent, DoneEvent)
    """
    async for event in old_stream:
        if event.type == "delta":
            yield TextEvent(text=event.text or "")
        elif event.type == "tool_call":
            if event.tool_call is not None:
                yield ToolCallEvent(
                    tool_call_id=event.tool_call.id,
                    name=event.tool_call.name,
                    arguments=event.tool_call.arguments,
                )
        elif event.type == "done":
            yield DoneEvent()
        elif event.type == "error":
            # Error events converted to DoneEvent
            # (agentkit DoneEvent has no error field; errors handled separately)
            yield DoneEvent()
