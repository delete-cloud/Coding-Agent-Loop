"""Wire protocol: base message types for communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol


@dataclass
class WireMessage:
    """Base for all wire messages."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Agent → UI messages
@dataclass
class TurnBegin(WireMessage):
    """A new turn is starting."""
    pass


@dataclass
class TurnEnd(WireMessage):
    """Turn has completed."""
    stop_reason: str = ""
    final_message: str | None = None


@dataclass
class StreamDelta(WireMessage):
    """Text delta from streaming LLM response."""
    text: str = ""


@dataclass
class ToolCallBegin(WireMessage):
    """Tool call is about to be executed."""
    call_id: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallEnd(WireMessage):
    """Tool call has completed."""
    call_id: str = ""
    result: str = ""


@dataclass
class ApprovalRequest(WireMessage):
    """Request user approval for a tool call."""
    call_id: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"


@dataclass
class ApprovalResponse(WireMessage):
    """User response to an approval request."""
    call_id: str = ""
    decision: Literal["approve", "deny"] = "approve"
    scope: Literal["once", "session", "always"] = "once"
    feedback: str | None = None


@dataclass
class StepInfo(WireMessage):
    """Information about current step."""
    step_number: int = 0
    max_steps: int = 0


@dataclass
class ErrorMessage(WireMessage):
    """Error message to display to the user."""
    content: str = ""


class WireConsumer(Protocol):
    """Protocol for wire message consumers (TUI, headless, HTTP)."""

    async def emit(self, msg: WireMessage) -> None:
        """Emit a message to the consumer."""
        ...

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval and wait for response."""
        ...
