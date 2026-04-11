"""Tests for tool execution timing."""

import time
from unittest.mock import Mock

import pytest
from rich.text import Text

from coding_agent.ui.rich_tui import ToolExecutionTracker


class TestToolExecutionTracker:
    """Tests for tool timing."""

    def test_start_and_end(self):
        """Test basic timing."""
        tracker = ToolExecutionTracker()

        tracker.start("call_1")
        time.sleep(0.01)  # 10ms
        duration = tracker.end("call_1")

        assert duration >= 0.01
        assert duration < 0.1  # Should be quick

    def test_end_without_start(self):
        """Test ending a call that was never started."""
        tracker = ToolExecutionTracker()

        duration = tracker.end("nonexistent")

        assert duration == 0.0

    def test_format_duration_under_1s(self):
        """Test color formatting for < 1s."""
        tracker = ToolExecutionTracker()

        text = tracker.format_duration(0.5)

        assert isinstance(text, Text)
        assert "(0.50s)" in str(text)
        assert "dim" in str(text.style).lower()

    def test_format_duration_1_to_5s(self):
        """Test color formatting for 1-5s."""
        tracker = ToolExecutionTracker()

        text = tracker.format_duration(2.5)

        assert isinstance(text, Text)
        assert "(2.50s)" in str(text)
        assert "yellow" in str(text.style).lower()

    def test_format_duration_over_5s(self):
        """Test color formatting for > 5s."""
        tracker = ToolExecutionTracker()

        text = tracker.format_duration(10.0)

        assert isinstance(text, Text)
        assert "(10.00s)" in str(text)
        assert "red" in str(text.style).lower()
        assert "⚠" in str(text)

    def test_format_duration_boundary_1s(self):
        """Test boundary at exactly 1s."""
        tracker = ToolExecutionTracker()

        # Exactly 1.0s should be yellow (1-5s range)
        text = tracker.format_duration(1.0)

        assert isinstance(text, Text)
        assert "yellow" in str(text.style).lower()

    def test_format_duration_boundary_5s(self):
        """Test boundary at exactly 5s."""
        tracker = ToolExecutionTracker()

        # Exactly 5.0s should be red (> 5s range)
        text = tracker.format_duration(5.0)

        assert isinstance(text, Text)
        assert "red" in str(text.style).lower()

    def test_multiple_calls(self):
        """Test tracking multiple calls."""
        tracker = ToolExecutionTracker()

        tracker.start("call_1")
        time.sleep(0.01)
        duration1 = tracker.end("call_1")

        tracker.start("call_2")
        time.sleep(0.02)
        duration2 = tracker.end("call_2")

        assert duration1 < duration2
        assert "call_1" in tracker._durations
        assert "call_2" in tracker._durations

    def test_reuse_call_id(self):
        """Test reusing a call_id (should reset timing)."""
        tracker = ToolExecutionTracker()

        tracker.start("call_1")
        time.sleep(0.01)
        duration1 = tracker.end("call_1")

        # Reuse the same call_id
        tracker.start("call_1")
        time.sleep(0.01)
        duration2 = tracker.end("call_1")

        # Both should be valid timings
        assert duration1 >= 0.01
        assert duration2 >= 0.01
