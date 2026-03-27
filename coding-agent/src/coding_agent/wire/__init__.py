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

__all__ = [
    # Message types
    "WireMessage",
    "StreamDelta",
    "ToolCallDelta",
    "ApprovalRequest",
    "ApprovalResponse",
    "TurnEnd",
    "CompletionStatus",
    # Wire implementations
    "LocalWire",
]
