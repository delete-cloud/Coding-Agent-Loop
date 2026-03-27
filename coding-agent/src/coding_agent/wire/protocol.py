"""Wire protocol message type definitions.

This module defines the typed message contracts used for communication
between the core agent loop and UI components (TUI or HTTP).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


@dataclass(kw_only=True)
class WireMessage:
    """Base class for all wire messages.
    
    Attributes:
        session_id: Unique identifier for the session
        timestamp: When the message was created
    """
    session_id: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(kw_only=True)
class StreamDelta(WireMessage):
    """Streaming content delta from agent.
    
    Attributes:
        content: The content delta text
        role: The role of the message sender (default: "assistant")
    """
    content: str
    role: str = "assistant"


@dataclass(kw_only=True)
class ToolCallDelta(WireMessage):
    """Tool call being streamed.
    
    Attributes:
        tool_name: Name of the tool being called
        arguments: Arguments for the tool call
        call_id: Unique identifier for this tool call
    """
    tool_name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(kw_only=True)
class ApprovalRequest(WireMessage):
    """Request for user approval.
    
    Attributes:
        request_id: Unique identifier for this approval request
        tool_call: The tool call requiring approval
        timeout_seconds: How long to wait for user response (default: 120)
    """
    request_id: str
    tool_call: ToolCallDelta
    timeout_seconds: int = 120


@dataclass(kw_only=True)
class ApprovalResponse(WireMessage):
    """User response to approval request.
    
    Attributes:
        request_id: Identifier matching the ApprovalRequest
        approved: Whether the request was approved
        feedback: Optional feedback from the user
    """
    request_id: str
    approved: bool
    feedback: str | None = None


class CompletionStatus(str, Enum):
    """Status values for turn completion."""
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(kw_only=True)
class TurnEnd(WireMessage):
    """End of current turn.
    
    Attributes:
        turn_id: Unique identifier for this turn
        completion_status: Status of the turn completion
    """
    turn_id: str
    completion_status: str  # "completed", "blocked", "error"
