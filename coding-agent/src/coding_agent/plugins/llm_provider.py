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
        else:
            raise ValueError(f"unsupported provider: {self._provider_name}")

        return self._instance


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
