"""Tests for performance metrics collection."""

import pytest
import time

from coding_agent.metrics import SessionMetrics, MetricsCollector, collector


class TestSessionMetrics:
    """Tests for SessionMetrics."""

    def test_session_creation(self):
        """Test creating a session metrics instance."""
        metrics = SessionMetrics(session_id="test-123")
        assert metrics.session_id == "test-123"
        assert metrics.start_time > 0
        assert metrics.api_calls == 0
        assert metrics.cache_hits == 0
        assert metrics.cache_misses == 0

    def test_record_tool_call(self):
        """Test recording tool calls."""
        metrics = SessionMetrics(session_id="test")
        
        metrics.record_tool_call("bash", 0.5)
        assert metrics.tool_calls["bash"] == 1
        assert len(metrics.tool_durations["bash"]) == 1
        assert metrics.tool_durations["bash"][0] == 0.5
        
        # Record another call to same tool
        metrics.record_tool_call("bash", 0.3)
        assert metrics.tool_calls["bash"] == 2
        assert len(metrics.tool_durations["bash"]) == 2

    def test_record_api_call(self):
        """Test recording API calls."""
        metrics = SessionMetrics(session_id="test")
        
        metrics.record_api_call(1.5)
        assert metrics.api_calls == 1
        assert metrics.api_latency_total == 1.5
        
        metrics.record_api_call(0.5)
        assert metrics.api_calls == 2
        assert metrics.api_latency_total == 2.0

    def test_record_cache(self):
        """Test recording cache hits and misses."""
        metrics = SessionMetrics(session_id="test")
        
        metrics.record_cache(hit=True)
        assert metrics.cache_hits == 1
        assert metrics.cache_misses == 0
        
        metrics.record_cache(hit=False)
        assert metrics.cache_hits == 1
        assert metrics.cache_misses == 1

    def test_cache_hit_rate(self):
        """Test cache hit rate calculation."""
        metrics = SessionMetrics(session_id="test")
        
        # No cache records yet
        assert metrics.cache_hit_rate == 0.0
        
        metrics.record_cache(hit=True)
        metrics.record_cache(hit=True)
        metrics.record_cache(hit=False)
        
        assert metrics.cache_hit_rate == 2 / 3

    def test_avg_api_latency(self):
        """Test average API latency calculation."""
        metrics = SessionMetrics(session_id="test")
        
        # No API calls yet
        assert metrics.avg_api_latency == 0.0
        
        metrics.record_api_call(1.0)
        metrics.record_api_call(3.0)
        
        assert metrics.avg_api_latency == 2.0

    def test_duration(self):
        """Test session duration calculation."""
        metrics = SessionMetrics(session_id="test")
        
        # Duration should be small but positive
        assert metrics.duration >= 0
        
        time.sleep(0.01)
        assert metrics.duration >= 0.01

    def test_to_dict(self):
        """Test converting metrics to dictionary."""
        metrics = SessionMetrics(session_id="test-123")
        metrics.record_tool_call("bash", 0.5)
        metrics.record_api_call(1.0)
        metrics.record_cache(hit=True)
        
        data = metrics.to_dict()
        
        assert data["session_id"] == "test-123"
        assert data["tool_calls"] == {"bash": 1}
        assert data["tools_total"] == 1
        assert data["api_calls"] == 1
        assert "duration" in data
        assert "avg_api_latency" in data
        assert "cache_hit_rate" in data


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def test_start_session(self):
        """Test starting a new session."""
        collector = MetricsCollector()
        
        metrics = collector.start_session("session-1")
        assert metrics.session_id == "session-1"
        assert "session-1" in collector.list_sessions()

    def test_get_session(self):
        """Test getting a session by ID."""
        collector = MetricsCollector()
        
        collector.start_session("session-1")
        metrics = collector.get_session("session-1")
        
        assert metrics is not None
        assert metrics.session_id == "session-1"
        
        # Non-existent session
        assert collector.get_session("non-existent") is None

    def test_list_sessions(self):
        """Test listing all sessions."""
        collector = MetricsCollector()
        
        assert collector.list_sessions() == []
        
        collector.start_session("session-1")
        collector.start_session("session-2")
        
        sessions = collector.list_sessions()
        assert len(sessions) == 2
        assert "session-1" in sessions
        assert "session-2" in sessions


class TestGlobalCollector:
    """Tests for the global collector instance."""

    def test_global_collector_exists(self):
        """Test that global collector exists."""
        assert collector is not None
        assert isinstance(collector, MetricsCollector)
