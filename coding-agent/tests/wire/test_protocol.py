"""Tests for wire protocol message types."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import pytest

from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ThinkingDelta,
    ToolCallDelta,
    TurnEnd,
    TurnStatusDelta,
    WireMessage,
)


class TestWireMessage:
    """Tests for base WireMessage class."""

    def test_wire_message_creation(self):
        """Test basic WireMessage creation."""
        msg = WireMessage(session_id="test-session-123")

        assert msg.session_id == "test-session-123"
        assert msg.agent_id == ""
        assert isinstance(msg.timestamp, datetime)

    def test_wire_message_custom_timestamp(self):
        """Test WireMessage with custom timestamp."""
        custom_time = datetime(2024, 1, 15, 10, 30, 0)
        msg = WireMessage(
            session_id="test-session",
            agent_id="child-1",
            timestamp=custom_time,
        )

        assert msg.agent_id == "child-1"
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

    def test_approval_request_legacy_tool_call_inherits_agent_id(self):
        msg = ApprovalRequest(
            session_id="session-1",
            agent_id="child-2",
            request_id="req-legacy",
            tool="delete_file",
            args={"path": "/important/file"},
        )

        assert msg.tool_call is not None
        assert msg.tool_call.agent_id == "child-2"

    def test_approval_request_syncs_call_id_and_request_id(self):
        msg = ApprovalRequest(
            session_id="session-1",
            call_id="call-legacy",
            tool="bash",
            args={"command": "pwd"},
        )

        assert msg.request_id == "call-legacy"
        assert msg.call_id == "call-legacy"
        assert msg.tool_call is not None
        assert msg.tool_call.call_id == "call-legacy"

    def test_approval_request_extracts_legacy_fields_from_tool_call(self):
        tool_call = ToolCallDelta(
            session_id="session-1",
            agent_id="child-7",
            tool_name="read_file",
            arguments={"path": "/tmp/x"},
            call_id="call-777",
        )

        msg = ApprovalRequest(
            session_id="session-1",
            tool_call=tool_call,
        )

        assert msg.tool == "read_file"
        assert msg.args == {"path": "/tmp/x"}
        assert msg.agent_id == "child-7"
        assert msg.call_id == "call-777"
        assert msg.request_id == "call-777"


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

    def test_approval_response_syncs_call_id_and_request_id(self):
        msg = ApprovalResponse(
            session_id="session-1",
            call_id="call-legacy",
            approved=True,
        )

        assert msg.request_id == "call-legacy"
        assert msg.call_id == "call-legacy"
        assert msg.decision == "approve"

    def test_approval_response_maps_legacy_decision_to_approved(self):
        msg = ApprovalResponse(
            session_id="session-1",
            request_id="req-456",
            decision="deny",
        )

        assert msg.approved is False
        assert msg.decision == "deny"


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
            completion_status=cast(CompletionStatus, cast(object, "completed")),
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


class TestThinkingDelta:
    def test_creation_with_text(self):
        msg = ThinkingDelta(text="reasoning about the problem")
        assert msg.text == "reasoning about the problem"

    def test_is_wire_message(self):
        msg = ThinkingDelta(text="thinking")
        assert isinstance(msg, WireMessage)

    def test_has_session_id_and_timestamp(self):
        msg = ThinkingDelta(session_id="sess-1", text="thought")
        assert msg.session_id == "sess-1"
        assert isinstance(msg.timestamp, datetime)

    def test_custom_timestamp(self):
        ts = datetime(2025, 6, 1, 12, 0, 0)
        msg = ThinkingDelta(text="thought", timestamp=ts)
        assert msg.timestamp == ts


class TestTurnStatusDelta:
    def test_creation_with_all_fields(self):
        msg = TurnStatusDelta(
            phase="thinking",
            elapsed_seconds=5.2,
            tokens_in=100,
            tokens_out=50,
            model_name="gpt-4",
            context_percent=42.5,
        )
        assert msg.phase == "thinking"
        assert msg.elapsed_seconds == 5.2
        assert msg.tokens_in == 100
        assert msg.tokens_out == 50
        assert msg.model_name == "gpt-4"
        assert msg.context_percent == 42.5

    def test_defaults(self):
        msg = TurnStatusDelta(phase="idle")
        assert msg.elapsed_seconds == 0.0
        assert msg.tokens_in == 0
        assert msg.tokens_out == 0
        assert msg.model_name == ""
        assert msg.context_percent == 0.0

    def test_is_wire_message(self):
        msg = TurnStatusDelta(phase="streaming")
        assert isinstance(msg, WireMessage)

    def test_phase_values(self):
        for phase in ("thinking", "streaming", "tool_call", "idle"):
            msg = TurnStatusDelta(phase=phase)
            assert msg.phase == phase

    def test_has_session_id(self):
        msg = TurnStatusDelta(session_id="sess-2", phase="tool_call")
        assert msg.session_id == "sess-2"


class TestMessageSerialization:
    """Tests for message serialization-like behavior."""

    def test_dataclass_asdict(self):
        """Test converting message to dict."""
        from dataclasses import asdict

        msg = StreamDelta(
            session_id="session-1",
            agent_id="child-1",
            content="test content",
        )

        data = asdict(msg)

        assert data["session_id"] == "session-1"
        assert data["agent_id"] == "child-1"
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
