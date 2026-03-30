"""Metrics collection for sessions and operations."""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class SessionMetrics:
    """Metrics for a single session (thread-safe).
    
    This class tracks various metrics during a session including:
    - Tool call counts and durations
    - API call counts and latencies
    - Cache hit/miss rates
    - Token usage
    
    All operations are protected by a threading.Lock to ensure thread safety.
    
    Example:
        ```python
        metrics = SessionMetrics(session_id="session-123")
        metrics.record_tool_call("search", 0.5)
        metrics.record_api_call(1.2)
        metrics.record_cache(hit=True)
        print(metrics.to_dict())
        ```
    """
    
    session_id: str
    start_time: float = field(default_factory=time.time)
    
    # Thread lock for protecting shared state
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    
    # Metric fields (internal use only)
    _tool_calls: dict[str, int] = field(
        default_factory=lambda: defaultdict(int), repr=False
    )
    _tool_durations: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list), repr=False
    )
    _api_calls: int = field(default=0, repr=False)
    _api_latency_total: float = field(default=0.0, repr=False)
    _cache_hits: int = field(default=0, repr=False)
    _cache_misses: int = field(default=0, repr=False)
    _tokens_input: int = field(default=0, repr=False)
    _tokens_output: int = field(default=0, repr=False)
    
    def record_tool_call(self, tool: str, duration: float) -> None:
        """Record a tool call with its duration.
        
        This method is thread-safe.
        
        Args:
            tool: The name of the tool that was called.
            duration: The duration of the tool call in seconds.
        """
        with self._lock:
            self._tool_calls[tool] += 1
            self._tool_durations[tool].append(duration)
    
    def record_api_call(self, latency: float) -> None:
        """Record an API call with its latency.
        
        This method is thread-safe.
        
        Args:
            latency: The API call latency in seconds.
        """
        with self._lock:
            self._api_calls += 1
            self._api_latency_total += latency
    
    def record_cache(self, hit: bool) -> None:
        """Record a cache hit or miss.
        
        This method is thread-safe.
        
        Args:
            hit: True if cache hit, False if cache miss.
        """
        with self._lock:
            if hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1
    
    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage.
        
        This method is thread-safe.
        
        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
        """
        with self._lock:
            self._tokens_input += input_tokens
            self._tokens_output += output_tokens
    
    @property
    def cache_hit_rate(self) -> float:
        """Calculate the cache hit rate.
        
        This property is thread-safe.
        
        Returns:
            The cache hit rate as a float between 0.0 and 1.0.
            Returns 0.0 if no cache operations have been recorded.
        """
        with self._lock:
            total = self._cache_hits + self._cache_misses
            return self._cache_hits / total if total > 0 else 0.0
    
    @property
    def avg_api_latency(self) -> float:
        """Calculate the average API latency.
        
        This property is thread-safe.
        
        Returns:
            The average API latency in seconds.
            Returns 0.0 if no API calls have been recorded.
        """
        with self._lock:
            return self._api_latency_total / self._api_calls if self._api_calls > 0 else 0.0
    
    def get_tool_stats(self, tool: str) -> dict[str, float]:
        """Get statistics for a specific tool.
        
        This method is thread-safe.
        
        Args:
            tool: The name of the tool.
            
        Returns:
            A dictionary with 'calls', 'avg_duration', and 'total_duration'.
        """
        with self._lock:
            durations = self._tool_durations.get(tool, [])
            calls = self._tool_calls.get(tool, 0)
            total = sum(durations) if durations else 0.0
            avg = total / len(durations) if durations else 0.0
            
            return {
                "calls": calls,
                "avg_duration": avg,
                "total_duration": total,
            }
    
    def to_dict(self) -> dict[str, object]:
        """Convert metrics to a dictionary.
        
        This method is thread-safe.
        
        Returns:
            A dictionary representation of all metrics.
        """
        with self._lock:
            total_tool_calls = sum(self._tool_calls.values())
            
            return {
                "session_id": self.session_id,
                "duration_sec": round(time.time() - self.start_time, 2),
                "tool_calls": dict(self._tool_calls),
                "tools_total": total_tool_calls,
                "api_calls": self._api_calls,
                "avg_api_latency_sec": round(self.avg_api_latency, 3),
                "cache_hit_rate": round(self.cache_hit_rate, 4),
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "tokens_input": self._tokens_input,
                "tokens_output": self._tokens_output,
                "tokens_total": self._tokens_input + self._tokens_output,
            }
    
    def reset(self) -> None:
        """Reset all metrics to their initial state.
        
        This method is thread-safe.
        """
        with self._lock:
            self._tool_calls.clear()
            self._tool_durations.clear()
            self._api_calls = 0
            self._api_latency_total = 0.0
            self._cache_hits = 0
            self._cache_misses = 0
            self._tokens_input = 0
            self._tokens_output = 0
            self.start_time = time.time()
