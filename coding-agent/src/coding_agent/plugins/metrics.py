"""SessionMetricsPlugin — in-memory performance metrics collection."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Callable


class SessionMetricsPlugin:
    state_key = "session_metrics"

    def __init__(self) -> None:
        self._turn_start: float | None = None
        self._steps_count: int = 0
        self._tool_calls: dict[str, int] = defaultdict(int)
        self._api_calls: int = 0
        self._api_latency_total: float = 0.0
        # Topic tracking
        self._current_topic_id: str | None = None
        self._topic_metrics: dict[str, dict[str, Any]] = {}
        # Token tracking (per-turn + session cumulative)
        self._turn_tokens_in: int = 0
        self._turn_tokens_out: int = 0
        self._session_tokens_in: int = 0
        self._session_tokens_out: int = 0

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "on_checkpoint": self.on_checkpoint,
            "on_session_event": self.on_session_event,
        }

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        if ctx is None:
            return

        now = time.time()
        if self._turn_start is None:
            self._turn_start = now

        tool_call_entries = ctx.tape.filter("tool_call")
        self._steps_count = len(tool_call_entries)

        self._tool_calls = defaultdict(int)
        for entry in tool_call_entries:
            name = entry.payload.get("name", "unknown")
            self._tool_calls[name] += 1

        total_turn_time = now - self._turn_start

        ctx.plugin_states[self.state_key] = {
            "steps_count": self._steps_count,
            "tool_calls": dict(self._tool_calls),
            "turn_start_time": self._turn_start,
            "total_turn_time": total_turn_time,
            "api_calls": self._api_calls,
            "api_latency_total": self._api_latency_total,
            "avg_api_latency": (
                self._api_latency_total / self._api_calls
                if self._api_calls > 0
                else 0.0
            ),
            "tokens_input": self._turn_tokens_in,
            "tokens_output": self._turn_tokens_out,
            "session_tokens_input": self._session_tokens_in,
            "session_tokens_output": self._session_tokens_out,
        }

    def on_session_event(
        self, event_type: str = "", payload: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        payload = payload or {}
        if event_type == "topic_start":
            self._current_topic_id = payload.get("topic_id")
        elif event_type == "topic_end":
            topic_id = payload.get("topic_id")
            if topic_id:
                self._topic_metrics[topic_id] = {
                    "steps_count": self._steps_count,
                    "tool_calls": dict(self._tool_calls),
                    "topic_id": topic_id,
                }

    def get_topic_metrics(self, topic_id: str) -> dict[str, Any] | None:
        return self._topic_metrics.get(topic_id)

    def get_all_topic_metrics(self) -> dict[str, dict[str, Any]]:
        return dict(self._topic_metrics)

    def get_metrics(self) -> dict[str, Any]:
        now = time.time()
        total_turn_time = (now - self._turn_start) if self._turn_start else 0.0
        return {
            "steps_count": self._steps_count,
            "tool_calls": dict(self._tool_calls),
            "turn_start_time": self._turn_start,
            "total_turn_time": total_turn_time,
            "api_calls": self._api_calls,
            "api_latency_total": self._api_latency_total,
            "avg_api_latency": (
                self._api_latency_total / self._api_calls
                if self._api_calls > 0
                else 0.0
            ),
            "tokens_input": self._turn_tokens_in,
            "tokens_output": self._turn_tokens_out,
            "session_tokens_input": self._session_tokens_in,
            "session_tokens_output": self._session_tokens_out,
        }

    def reset_turn(self) -> None:
        self._turn_start = None
        self._steps_count = 0
        self._tool_calls = defaultdict(int)
        self._turn_tokens_in = 0
        self._turn_tokens_out = 0

    def record_api_call(self, latency: float) -> None:
        self._api_calls += 1
        self._api_latency_total += latency

    def record_token_usage(self, input_tokens: int, output_tokens: int) -> None:
        self._turn_tokens_in += input_tokens
        self._turn_tokens_out += output_tokens
        self._session_tokens_in += input_tokens
        self._session_tokens_out += output_tokens
