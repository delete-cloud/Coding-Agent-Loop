from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from agentkit.providers.models import StreamEvent


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def max_context_size(self) -> int: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]: ...
