"""Tests for ToolResultDelta wire message and adapter handling."""

import pytest

from coding_agent.wire.protocol import ToolResultDelta


class TestToolResultDelta:
    def test_creation(self):
        msg = ToolResultDelta(
            call_id="call_123",
            tool_name="bash",
            result="output",
        )
        assert msg.call_id == "call_123"
        assert msg.tool_name == "bash"
        assert msg.result == "output"
        assert msg.is_error is False

    def test_error_result(self):
        msg = ToolResultDelta(
            call_id="call_err",
            tool_name="bash",
            result="Error: fail",
            is_error=True,
        )
        assert msg.is_error is True

    def test_inherits_wire_message(self):
        from coding_agent.wire.protocol import WireMessage

        msg = ToolResultDelta(call_id="c1", tool_name="t", result="r")
        assert isinstance(msg, WireMessage)
