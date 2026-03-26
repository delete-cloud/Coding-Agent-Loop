"""Base provider types and protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol


@dataclass
class ToolCall:
    """A tool call from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class StreamEvent:
    """Event from streaming LLM response."""
    type: Literal["delta", "tool_call", "done", "error"]
    text: str | None = None
    tool_call: ToolCall | None = None
    error: str | None = None


@dataclass
class ToolSchema:
    """JSON schema for a tool."""
    type: Literal["function"] = "function"
    function: dict[str, Any] = None  # type: ignore

    def __post_init__(self):
        if self.function is None:
            self.function = {}


class ChatProvider(Protocol):
    """Protocol for chat providers (OpenAI, Anthropic, etc.)."""

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream LLM response.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tool schemas
            **kwargs: Additional provider-specific options
            
        Yields:
            StreamEvent objects (delta, tool_call, done, error)
        """
        ...

    @property
    def model_name(self) -> str:
        """Name of the model being used."""
        ...

    @property
    def max_context_size(self) -> int:
        """Maximum context size in tokens."""
        ...


class StreamingResponse:
    """Accumulated streaming response."""

    def __init__(self):
        self.text_parts: list[str] = []
        self.tool_calls: list[ToolCall] = []
        self._current_tool_call: dict[str, Any] | None = None

    def add_delta(self, text: str) -> None:
        """Add a text delta."""
        self.text_parts.append(text)

    def add_tool_call(self, tool_call: ToolCall) -> None:
        """Add a complete tool call."""
        self.tool_calls.append(tool_call)

    @property
    def text(self) -> str:
        """Get full text content."""
        return "".join(self.text_parts)

    @property
    def has_tool_calls(self) -> bool:
        """Check if response has tool calls."""
        return len(self.tool_calls) > 0

    def __repr__(self) -> str:
        return f"StreamingResponse(text={self.text!r}, tool_calls={self.tool_calls})"
