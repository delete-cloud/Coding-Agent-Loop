"""Tests for wire protocol message types."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)


class TestWireMessage:
    """Tests for base WireMessage class."""

    def test_wire_message_creation(self):
        """Test basic WireMessage creation."""
        msg = WireMessage(session_id="test-session-123")
        
        assert msg.session_id == "test-session-123"
        assert isinstance(msg.timestamp, datetime)
    
    def test_wire_message_custom_timestamp(self):
        """Test WireMessage with custom timestamp."""
        custom_time = datetime(2024, 1, 15, 10, 30, 0)
        msg = WireMessage(
            session_id="test-session",
            timestamp=custom_time,
        )
        
        assert msg.timestamp == custom_time


class TestStreamDelta:
    """Tests for StreamDelta message."""

    def test_stream_delta_defaults(self):
        """Test StreamDelta with default values."""
        msg = StreamDelta(
            session_id="session-1",
            content="Hello, world!",
        )
        
        assert msg.session_id == "session-1"
        assert msg.content == "Hello, world!"
        assert msg.role == "assistant"
        assert isinstance(msg.timestamp, datetime)

    def test_stream_delta_custom_role(self):
        """Test StreamDelta with custom role."""
        msg = StreamDelta(
            session_id="session-1",
            content="Some content",
            role="user",
        )
        
        assert msg.role == "user"

    def test_stream_delta_is_wire_message(self):
        """Test StreamDelta is a WireMessage subclass."""
        msg = StreamDelta(
            session_id="session-1",
            content="test",
        )
        
        assert isinstance(msg, WireMessage)


class TestToolCallDelta:
    """Tests for ToolCallDelta message."""

    def test_tool_call_delta_creation(self):
        """Test ToolCallDelta creation."""
        args: dict[str, Any] = {"path": "/tmp/file.txt", "content": "hello"}
        msg = ToolCallDelta(
            session_id="session-1",
            tool_name="write_file",
            arguments=args,
            call_id="call-123",
        )
        
        assert msg.session_id == "session-1"
        assert msg.tool_name == "write_file"
        assert msg.arguments == args
        assert msg.call_id == "call-123"

    def test_tool_call_delta_empty_args(self):
        """Test ToolCallDelta with empty arguments."""
        msg = ToolCallDelta(
            session_id="session-1",
            tool_name="list_tools",
            arguments={},
            call_id="call-456",
        )
        
        assert msg.arguments == {}

    def test_tool_call_delta_nested_args(self):
        """Test ToolCallDelta with nested arguments."""
        args: dict[str, Any] = {
            "config": {"timeout": 30, "retry": True},
            "items": ["a", "b", "c"],
        }
        msg = ToolCallDelta(
            session_id="session-1",
            tool_name="complex_tool",
            arguments=args,
            call_id="call-789",
        )
        
        assert msg.arguments["config"]["timeout"] == 30
        assert msg.arguments["items"] == ["a", "b", "c"]


class TestApprovalRequest:
    """Tests for ApprovalRequest message."""

    def test_approval_request_defaults(self):
        """Test ApprovalRequest with default timeout."""
        tool_call = ToolCallDelta(
            session_id="session-1",
            tool_name="write_file",
            arguments={"path": "/etc/passwd"},
            call_id="call-123",
        )
        msg = ApprovalRequest(
            session_id="session-1",
            request_id="req-456",
            tool_call=tool_call,
        )
        
        assert msg.session_id == "session-1"
        assert msg.request_id == "req-456"
        assert msg.tool_call == tool_call
        assert msg.timeout_seconds == 120

    def test_approval_request_custom_timeout(self):
        """Test ApprovalRequest with custom timeout."""
        tool_call = ToolCallDelta(
            session_id="session-1",
            tool_name="delete_file",
            arguments={"path": "/important/file"},
            call_id="call-789",
        )
        msg = ApprovalRequest(
            session_id="session-1",
            request_id="req-999",
            tool_call=tool_call,
            timeout_seconds=60,
        )
        
        assert msg.timeout_seconds == 60


class TestApprovalResponse:
    """Tests for ApprovalResponse message."""

    def test_approval_response_approved(self):
        """Test approved ApprovalResponse."""
        msg = ApprovalResponse(
            session_id="session-1",
            request_id="req-456",
            approved=True,
        )
        
        assert msg.session_id == "session-1"
        assert msg.request_id == "req-456"
        assert msg.approved is True
        assert msg.feedback is None

    def test_approval_response_denied(self):
        """Test denied ApprovalResponse with feedback."""
        msg = ApprovalResponse(
            session_id="session-1",
            request_id="req-456",
            approved=False,
            feedback="This operation looks dangerous",
        )
        
        assert msg.approved is False
        assert msg.feedback == "This operation looks dangerous"

    def test_approval_response_no_feedback(self):
        """Test denied ApprovalResponse without feedback."""
        msg = ApprovalResponse(
            session_id="session-1",
            request_id="req-456",
            approved=False,
        )
        
        assert msg.approved is False
        assert msg.feedback is None


class TestTurnEnd:
    """Tests for TurnEnd message."""

    def test_turn_end_completed(self):
        """Test TurnEnd with completed status."""
        msg = TurnEnd(
            session_id="session-1",
            turn_id="turn-123",
            completion_status=CompletionStatus.COMPLETED,
        )
        
        assert msg.session_id == "session-1"
        assert msg.turn_id == "turn-123"
        assert msg.completion_status == "completed"

    def test_turn_end_blocked(self):
        """Test TurnEnd with blocked status."""
        msg = TurnEnd(
            session_id="session-1",
            turn_id="turn-456",
            completion_status=CompletionStatus.BLOCKED,
        )
        
        assert msg.completion_status == "blocked"

    def test_turn_end_error(self):
        """Test TurnEnd with error status."""
        msg = TurnEnd(
            session_id="session-1",
            turn_id="turn-789",
            completion_status=CompletionStatus.ERROR,
        )
        
        assert msg.completion_status == "error"

    def test_turn_end_string_status(self):
        """Test TurnEnd with raw string status."""
        msg = TurnEnd(
            session_id="session-1",
            turn_id="turn-abc",
            completion_status="completed",
        )
        
        assert msg.completion_status == "completed"


class TestCompletionStatus:
    """Tests for CompletionStatus enum."""

    def test_completion_status_values(self):
        """Test CompletionStatus enum values."""
        assert CompletionStatus.COMPLETED == "completed"
        assert CompletionStatus.BLOCKED == "blocked"
        assert CompletionStatus.ERROR == "error"

    def test_completion_status_is_str(self):
        """Test CompletionStatus is a str subclass."""
        assert isinstance(CompletionStatus.COMPLETED, str)


class TestMessageSerialization:
    """Tests for message serialization-like behavior."""

    def test_dataclass_asdict(self):
        """Test converting message to dict."""
        from dataclasses import asdict
        
        msg = StreamDelta(
            session_id="session-1",
            content="test content",
        )
        
        data = asdict(msg)
        
        assert data["session_id"] == "session-1"
        assert data["content"] == "test content"
        assert data["role"] == "assistant"
        assert "timestamp" in data

    def test_nested_dataclass_asdict(self):
        """Test converting ApprovalRequest with nested ToolCallDelta to dict."""
        from dataclasses import asdict
        
        tool_call = ToolCallDelta(
            session_id="session-1",
            tool_name="test_tool",
            arguments={"arg1": "value1"},
            call_id="call-123",
        )
        request = ApprovalRequest(
            session_id="session-1",
            request_id="req-456",
            tool_call=tool_call,
        )
        
        data = asdict(request)
        
        assert data["session_id"] == "session-1"
        assert data["request_id"] == "req-456"
        assert data["timeout_seconds"] == 120
        assert data["tool_call"]["tool_name"] == "test_tool"
        assert data["tool_call"]["arguments"] == {"arg1": "value1"}
