from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    kind: str = field(init=False)


@dataclass(frozen=True)
class TextEvent(StreamEvent):
    text: str = ""
    kind: str = field(init=False, default="text")


@dataclass(frozen=True)
class ToolCallEvent(StreamEvent):
    tool_call_id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    kind: str = field(init=False, default="tool_call")


@dataclass(frozen=True)
class ThinkingEvent(StreamEvent):
    text: str = ""
    kind: str = field(init=False, default="thinking")


@dataclass(frozen=True)
class ToolResultEvent(StreamEvent):
    tool_call_id: str = ""
    name: str = ""
    result: str = ""
    is_error: bool = False
    kind: str = field(init=False, default="tool_result")


@dataclass(frozen=True)
class UsageEvent(StreamEvent):
    input_tokens: int = 0
    output_tokens: int = 0
    provider_name: str = ""
    kind: str = field(init=False, default="usage")


@dataclass(frozen=True)
class DoneEvent(StreamEvent):
    kind: str = field(init=False, default="done")
