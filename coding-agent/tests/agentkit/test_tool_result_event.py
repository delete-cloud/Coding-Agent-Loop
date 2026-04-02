"""Tests for ToolResultEvent."""

import pytest

from agentkit.providers.models import ToolResultEvent


class TestToolResultEvent:
    def test_creation(self):
        event = ToolResultEvent(
            tool_call_id="call_123",
            name="bash",
            result="hello world",
        )
        assert event.kind == "tool_result"
        assert event.tool_call_id == "call_123"
        assert event.name == "bash"
        assert event.result == "hello world"

    def test_frozen(self):
        event = ToolResultEvent(tool_call_id="x", name="y", result="z")
        with pytest.raises(AttributeError):
            event.name = "changed"

    def test_is_error_default_false(self):
        event = ToolResultEvent(tool_call_id="x", name="y", result="z")
        assert event.is_error is False

    def test_is_error_true(self):
        event = ToolResultEvent(tool_call_id="x", name="y", result="err", is_error=True)
        assert event.is_error is True

    def test_importable_from_providers_package(self):
        from agentkit.providers import ToolResultEvent as TR1

        assert TR1 is ToolResultEvent

    def test_importable_from_agentkit_package(self):
        from agentkit import ToolResultEvent as TR2

        assert TR2 is ToolResultEvent
