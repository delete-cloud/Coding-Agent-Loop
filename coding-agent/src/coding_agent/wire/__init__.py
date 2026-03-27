"""Wire protocol module for agent-to-UI communication.

This module provides typed message contracts and wire implementations
for communication between the core agent loop and UI components.

Example:
    >>> from coding_agent.wire import StreamDelta, LocalWire
    >>> wire = LocalWire("session-123")
    >>> await wire.send(StreamDelta(session_id="session-123", content="Hello"))
"""

from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)

# Additional message types used by loop.py (backward compatible)
from dataclasses import dataclass, field
from typing import Any


@dataclass(kw_only=True)
class TurnBegin(WireMessage):
    """A new turn is starting."""
    session_id: str = ""


@dataclass(kw_only=True)
class ToolCallBegin(WireMessage):
    """Tool call is about to be executed."""
    session_id: str = ""
    call_id: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class ToolCallEnd(WireMessage):
    """Tool call has completed."""
    session_id: str = ""
    call_id: str = ""
    result: str = ""


@dataclass(kw_only=True)
class StepInfo(WireMessage):
    """Information about current step."""
    session_id: str = ""
    step_number: int = 0
    max_steps: int = 0


@dataclass(kw_only=True)
class ErrorMessage(WireMessage):
    """Error message to display to the user."""
    session_id: str = ""
    content: str = ""


__all__ = [
    # Message types from protocol
    "WireMessage",
    "StreamDelta",
    "ToolCallDelta",
    "ApprovalRequest",
    "ApprovalResponse",
    "TurnEnd",
    "CompletionStatus",
    # Additional types for loop.py
    "TurnBegin",
    "ToolCallBegin",
    "ToolCallEnd",
    "StepInfo",
    "ErrorMessage",
    # Wire implementations
    "LocalWire",
]
