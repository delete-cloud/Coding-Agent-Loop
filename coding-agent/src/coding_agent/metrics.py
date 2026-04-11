"""Performance metrics collection."""

from dataclasses import dataclass, field
from typing import Dict, List
from collections import defaultdict
import time


@dataclass
class SessionMetrics:
    """Metrics for a single session."""

    session_id: str
    start_time: float = field(default_factory=time.time)

    # Tool metrics
    tool_calls: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tool_durations: Dict[str, List[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    # API metrics
    api_calls: int = 0
    api_latency_total: float = 0.0

    # Cache metrics
    cache_hits: int = 0
    cache_misses: int = 0

    # Token usage
    tokens_input: int = 0
    tokens_output: int = 0

    def record_tool_call(self, tool: str, duration: float) -> None:
        """Record a tool call."""
        self.tool_calls[tool] += 1
        self.tool_durations[tool].append(duration)

    def record_api_call(self, latency: float) -> None:
        """Record an API call."""
        self.api_calls += 1
        self.api_latency_total += latency

    def record_cache(self, hit: bool) -> None:
        """Record cache hit/miss."""
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    @property
    def cache_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    @property
    def avg_api_latency(self) -> float:
        """Calculate average API latency."""
        return self.api_latency_total / self.api_calls if self.api_calls > 0 else 0.0

    @property
    def duration(self) -> float:
        """Calculate session duration."""
        return time.time() - self.start_time

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "duration": f"{self.duration:.1f}s",
            "tool_calls": dict(self.tool_calls),
            "tools_total": sum(self.tool_calls.values()),
            "api_calls": self.api_calls,
            "avg_api_latency": f"{self.avg_api_latency:.2f}s",
            "cache_hit_rate": f"{self.cache_hit_rate:.1%}",
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
        }


class MetricsCollector:
    """Global metrics collector."""

    def __init__(self):
        self._sessions: Dict[str, SessionMetrics] = {}

    def start_session(self, session_id: str) -> SessionMetrics:
        """Start tracking a new session."""
        metrics = SessionMetrics(session_id=session_id)
        self._sessions[session_id] = metrics
        return metrics

    def get_session(self, session_id: str) -> SessionMetrics | None:
        """Get metrics for a session."""
        return self._sessions.get(session_id)

    def list_sessions(self) -> List[str]:
        """List all tracked sessions."""
        return list(self._sessions.keys())


# Global instance
collector = MetricsCollector()
