"""Wire protocol message type definitions.

This module defines the typed message contracts used for communication
between the core agent loop and UI components (TUI or HTTP).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


@dataclass(kw_only=True)
class WireMessage:
    """Base class for all wire messages.

    Attributes:
        session_id: Unique identifier for the session
        agent_id: Identifier for the emitting agent within the session
        timestamp: When the message was created
    """

    session_id: str = ""  # Default to empty string for backward compatibility
    agent_id: str = ""
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
class ToolResultDelta(WireMessage):
    """Tool execution result.

    Attributes:
        call_id: ID matching the original ToolCallDelta
        tool_name: Name of the tool that was executed
        result: The raw tool execution result payload
        display_result: A redacted or user-safe display string
        is_error: Whether the result is an error
    """

    call_id: str
    tool_name: str
    result: Any
    display_result: str = ""
    is_error: bool = False


@dataclass(kw_only=True)
class ThinkingDelta(WireMessage):
    text: str


@dataclass(kw_only=True)
class TurnStatusDelta(WireMessage):
    phase: str  # "thinking" | "streaming" | "tool_call" | "idle"
    elapsed_seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    model_name: str = ""
    context_percent: float = 0.0


@dataclass(kw_only=True)
class ApprovalRequest(WireMessage):
    """Request for user approval.

    Supports both new protocol format (request_id, tool_call) and
    legacy format (call_id, tool, args, risk_level).

    Attributes:
        request_id: Unique identifier for this approval request
        tool_call: The tool call requiring approval
        timeout_seconds: How long to wait for user response (default: 120)
        # Legacy fields (for backward compatibility):
        call_id: Alias for request_id
        tool: Tool name (legacy, use tool_call.tool_name)
        args: Tool arguments (legacy, use tool_call.arguments)
        risk_level: Risk level string (legacy)
    """

    request_id: str = ""
    tool_call: ToolCallDelta | None = None
    timeout_seconds: int = 120
    # Legacy fields for backward compatibility
    call_id: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"

    def __post_init__(self):
        """Sync legacy and new format fields."""
        # Sync request_id and call_id
        if self.call_id and not self.request_id:
            self.request_id = self.call_id
        elif self.request_id and not self.call_id:
            self.call_id = self.request_id

        # Build tool_call from legacy fields if needed
        if self.tool_call is None and self.tool:
            self.tool_call = ToolCallDelta(
                session_id=self.session_id,
                tool_name=self.tool,
                arguments=self.args,
                call_id=self.call_id or self.request_id,
            )
        # Extract legacy fields from tool_call if available
        elif self.tool_call and not self.tool:
            self.tool = self.tool_call.tool_name
            self.args = self.tool_call.arguments
            if not self.call_id:
                self.call_id = self.tool_call.call_id
            if not self.request_id:
                self.request_id = self.tool_call.call_id


@dataclass(kw_only=True)
class ApprovalResponse(WireMessage):
    """User response to approval request.

    Supports both new protocol format (request_id, approved) and
    legacy format (call_id, decision).

    Attributes:
        request_id: Identifier matching the ApprovalRequest
        approved: Whether the request was approved
        feedback: Optional feedback from the user
        # Legacy fields (for backward compatibility):
        call_id: Alias for request_id
        decision: "approve" or "deny" (legacy, maps to approved)
        scope: Approval scope (legacy)
    """

    request_id: str = ""
    approved: bool = True
    feedback: str | None = None
    # Legacy fields for backward compatibility
    call_id: str = ""
    # Use empty string as sentinel for "not explicitly set"
    decision: str = ""  # Literal["approve", "deny"] = ""
    scope: Literal["once", "session", "always"] = "once"

    def __post_init__(self):
        """Sync legacy and new format fields."""
        # Sync request_id and call_id
        if self.call_id and not self.request_id:
            self.request_id = self.call_id
        elif self.request_id and not self.call_id:
            self.call_id = self.request_id

        # Sync decision and approved
        if self.decision == "deny":
            self.approved = False
        elif self.decision == "approve":
            self.approved = True
        else:
            # decision not explicitly set, derive from approved
            self.decision = "approve" if self.approved else "deny"


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
    completion_status: CompletionStatus
