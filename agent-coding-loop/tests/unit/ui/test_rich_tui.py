"""Tests for ToolExecutionTracker."""

import time
import pytest
from coding_agent.ui.rich_tui import ToolExecutionTracker


class TestToolExecutionTracker:
    """Test suite for ToolExecutionTracker."""
    
    def test_basic_tracking(self):
        """Test basic start/end tracking."""
        tracker = ToolExecutionTracker()
        
        tracker.start("call-1")
        time.sleep(0.01)  # Small delay
        duration = tracker.end("call-1")
        
        assert duration >= 0.01
        assert tracker.get_duration("call-1") == duration
    
    def test_end_without_start(self):
        """Test ending a call that was never started."""
        tracker = ToolExecutionTracker()
        
        duration = tracker.end("nonexistent")
        assert duration == 0.0
    
    def test_get_duration_nonexistent(self):
        """Test getting duration for non-existent call."""
        tracker = ToolExecutionTracker()
        
        duration = tracker.get_duration("nonexistent")
        assert duration == 0.0
    
    def test_empty_call_id_raises(self):
        """Test that empty call_id raises ValueError."""
        tracker = ToolExecutionTracker()
        
        with pytest.raises(ValueError, match="call_id cannot be empty"):
            tracker.start("")
    
    def test_clear(self):
        """Test clearing all tracked entries."""
        tracker = ToolExecutionTracker()
        
        tracker.start("call-1")
        tracker.end("call-1")
        tracker.start("call-2")
        
        tracker.clear()
        
        assert len(tracker._start_times) == 0
        assert len(tracker._durations) == 0
        assert tracker.get_duration("call-1") == 0.0
    
    def test_multiple_calls(self):
        """Test tracking multiple calls."""
        tracker = ToolExecutionTracker()
        
        tracker.start("call-1")
        tracker.start("call-2")
        
        duration1 = tracker.end("call-1")
        duration2 = tracker.end("call-2")
        
        assert duration1 >= 0
        assert duration2 >= 0
        assert tracker.get_duration("call-1") == duration1
        assert tracker.get_duration("call-2") == duration2
    
    def test_lru_behavior(self):
        """Test LRU behavior when re-accessing entries."""
        tracker = ToolExecutionTracker()
        tracker._max_entries = 3
        
        tracker.start("call-1")
        tracker.start("call-2")
        tracker.start("call-3")
        
        # Re-access call-1 to make it recently used
        tracker.start("call-1")
        tracker.end("call-1")
        
        # Add more calls to trigger cleanup
        tracker.start("call-4")
        tracker.end("call-4")
        tracker.start("call-5")
        tracker.end("call-5")
        
        # call-1 should still exist (was accessed recently)
        assert "call-1" in tracker._durations
    
    def test_memory_limit_cleanup(self):
        """Test that old entries are cleaned up when limit reached."""
        tracker = ToolExecutionTracker()
        tracker._max_entries = 3
        
        # Add entries up to the limit
        tracker.start("call-1")
        tracker.end("call-1")
        tracker.start("call-2")
        tracker.end("call-2")
        
        # This should trigger cleanup of oldest completed entry
        tracker.start("call-3")
        tracker.start("call-4")  # Should trigger cleanup
        
        # Total entries should stay within limit
        total_entries = len(tracker._start_times) + len(tracker._durations)
        assert total_entries <= tracker._max_entries
    
    def test_reuse_call_id(self):
        """Test reusing the same call_id."""
        tracker = ToolExecutionTracker()
        
        tracker.start("call-1")
        duration1 = tracker.end("call-1")
        
        # Reuse the same call_id
        tracker.start("call-1")
        time.sleep(0.01)
        duration2 = tracker.end("call-1")
        
        assert duration2 >= 0.01
        # The second call overwrites the first
        assert tracker.get_duration("call-1") == duration2
