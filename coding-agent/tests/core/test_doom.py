"""Tests for DoomDetector."""

import pytest

from coding_agent.core.doom import DoomDetector


class TestDoomDetector:
    def test_threshold_of_3_by_default(self):
        """Default threshold should be 3."""
        detector = DoomDetector()
        assert detector.threshold == 3

    def test_custom_threshold_works(self):
        """Custom threshold should be respected."""
        detector = DoomDetector(threshold=5)
        assert detector.threshold == 5

    def test_detection_after_threshold_repeated_calls(self):
        """Doom loop should be detected after threshold repeated calls."""
        detector = DoomDetector(threshold=3)
        
        # First call - count=1, no doom
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        # Second call - count=2, no doom
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        # Third call - count=3, DOOM detected
        assert detector.observe("read_file", {"path": "/foo.py"}) is True
        # Fourth call - count=4, still DOOM
        assert detector.observe("read_file", {"path": "/foo.py"}) is True

    def test_reset_counter_on_different_tool(self):
        """Counter should reset when a different tool is called."""
        detector = DoomDetector(threshold=3)
        
        # Two calls to read_file
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        
        # Different tool resets counter (count=1 after this)
        assert detector.observe("write_file", {"path": "/bar.py", "content": "x"}) is False
        
        # Need 2 more calls to reach threshold of 3
        assert detector.observe("write_file", {"path": "/bar.py", "content": "x"}) is False
        assert detector.observe("write_file", {"path": "/bar.py", "content": "x"}) is True

    def test_reset_counter_on_different_args(self):
        """Counter should reset when same tool has different args."""
        detector = DoomDetector(threshold=3)
        
        # Two calls to read_file with /foo.py
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        
        # Same tool but different args - resets counter (count=1 after this)
        assert detector.observe("read_file", {"path": "/bar.py"}) is False
        
        # Need 2 more calls to reach threshold of 3
        assert detector.observe("read_file", {"path": "/bar.py"}) is False
        assert detector.observe("read_file", {"path": "/bar.py"}) is True

    def test_near_misses_reset_counter(self):
        """Same tool with different args (near-misses) should reset counter."""
        detector = DoomDetector(threshold=3)
        
        # Two calls to read_file with /foo.py
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        
        # Near-miss: same tool, different path - resets counter
        assert detector.observe("read_file", {"path": "/bar.py"}) is False
        
        # Another near-miss - resets counter again
        assert detector.observe("read_file", {"path": "/baz.py"}) is False
        
        # Back to /foo.py - counter was reset, so need 3 more calls
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        assert detector.observe("read_file", {"path": "/foo.py"}) is True

    def test_args_order_independence(self):
        """Different arg order should be treated as same args (sorted keys)."""
        detector = DoomDetector(threshold=3)
        
        # First call with one order
        assert detector.observe("test_tool", {"a": 1, "b": 2}) is False
        # Second call with different order - should count as same
        assert detector.observe("test_tool", {"b": 2, "a": 1}) is False
        # Third call - doom detected
        assert detector.observe("test_tool", {"a": 1, "b": 2}) is True

    def test_different_values_count_as_different_args(self):
        """Same keys with different values should be different args."""
        detector = DoomDetector(threshold=3)
        
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        assert detector.observe("read_file", {"path": "/foo.py"}) is False
        
        # Same key, different value - resets counter (count=1 after this)
        assert detector.observe("read_file", {"path": "/bar.py"}) is False
        
        # Need 2 more calls to reach threshold of 3
        assert detector.observe("read_file", {"path": "/bar.py"}) is False
        assert detector.observe("read_file", {"path": "/bar.py"}) is True

    def test_complex_args_hashing(self):
        """Complex nested args should be properly hashed."""
        detector = DoomDetector(threshold=3)
        
        args = {"data": {"nested": [1, 2, 3], "flag": True}}
        
        assert detector.observe("process", args) is False
        assert detector.observe("process", args) is False
        assert detector.observe("process", args) is True

    def test_initial_state(self):
        """Detector should start with count 0 and no last tool."""
        detector = DoomDetector()
        assert detector.count == 0
        assert detector.last_tool is None
        assert detector.last_args_hash is None
