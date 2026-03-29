"""Rich TUI components for coding agent."""

import time
from collections import OrderedDict


class ToolExecutionTracker:
    """Track tool execution times with memory limit.
    
    This class tracks the start and end times of tool calls to calculate
    their durations. It implements an LRU (Least Recently Used) cache
    strategy to prevent memory leaks by limiting the maximum number of
    tracked entries.
    
    Attributes:
        _max_entries: Maximum number of entries to track before cleanup.
        _start_times: OrderedDict storing start times for active tool calls.
        _durations: OrderedDict storing calculated durations for completed calls.
        _access_order: OrderedDict tracking LRU order of all entries.
    
    Example:
        ```python
        tracker = ToolExecutionTracker()
        tracker.start("call-1")
        # ... tool executes ...
        duration = tracker.end("call-1")
        print(f"Tool took {duration:.3f} seconds")
        ```
    """
    
    _max_entries: int = 1000  # 防止内存泄漏
    
    def __init__(self) -> None:
        """Initialize the tracker with empty storage."""
        # Use OrderedDict for efficient LRU implementation
        self._start_times: OrderedDict[str, float] = OrderedDict()
        self._durations: OrderedDict[str, float] = OrderedDict()
        # Unified access order tracking for LRU across both dicts
        self._access_order: OrderedDict[str, None] = OrderedDict()
    
    def _update_access(self, call_id: str) -> None:
        """Update access order for LRU tracking.
        
        Moves the call_id to the end of the access order (most recently used).
        
        Args:
            call_id: Unique identifier for the tool call.
        """
        if call_id in self._access_order:
            self._access_order.move_to_end(call_id)
        else:
            self._access_order[call_id] = None
    
    def _cleanup_old_entries(self) -> None:
        """Remove oldest entries when limit reached.
        
        Removes the oldest entries based on LRU order until the total count
        is below _max_entries.
        """
        total_entries = len(self._start_times) + len(self._durations)
        while total_entries >= self._max_entries and self._access_order:
            oldest = next(iter(self._access_order))
            self._access_order.pop(oldest, None)
            self._start_times.pop(oldest, None)
            self._durations.pop(oldest, None)
            total_entries = len(self._start_times) + len(self._durations)
    
    def start(self, call_id: str) -> None:
        """Start tracking a tool call.
        
        Records the current time as the start time for the given call_id.
        If the maximum number of entries is reached, old entries are cleaned up.
        
        Args:
            call_id: Unique identifier for the tool call.
        
        Raises:
            ValueError: If call_id is empty.
        """
        if not call_id:
            raise ValueError("call_id cannot be empty")
        
        if len(self._start_times) + len(self._durations) >= self._max_entries:
            self._cleanup_old_entries()
        
        # Update access order (LRU)
        self._update_access(call_id)
        
        self._start_times[call_id] = time.perf_counter()
    
    def end(self, call_id: str) -> float:
        """End tracking and return duration.
        
        Calculates the duration since start was called for the given call_id,
        stores it, and removes the start time entry.
        
        Args:
            call_id: Unique identifier for the tool call.
        
        Returns:
            The duration in seconds. Returns 0.0 if call_id was not found
            (e.g., if it was cleaned up due to memory limits).
        """
        if call_id not in self._start_times:
            return 0.0
        
        duration = time.perf_counter() - self._start_times[call_id]
        del self._start_times[call_id]
        
        # Store duration and update access order
        self._durations[call_id] = duration
        self._update_access(call_id)
        
        return duration
    
    def get_duration(self, call_id: str) -> float:
        """Get the duration for a completed tool call.
        
        Args:
            call_id: Unique identifier for the tool call.
        
        Returns:
            The duration in seconds, or 0.0 if not found.
        """
        return self._durations.get(call_id, 0.0)
    
    def clear(self) -> None:
        """Clear all tracked entries."""
        self._start_times.clear()
        self._durations.clear()
        self._access_order.clear()
