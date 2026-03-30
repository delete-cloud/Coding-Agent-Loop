"""Tests for SessionMetricsPlugin — performance metrics collection via on_checkpoint hook."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.plugins.metrics import SessionMetricsPlugin


def _make_tool_call(name: str, arguments: dict[str, Any] | None = None) -> Entry:
    """Helper to create a tool_call entry."""
    return Entry(
        kind="tool_call",
        payload={
            "id": "call_001",
            "name": name,
            "arguments": arguments or {},
            "role": "assistant",
        },
    )


def _make_tool_result(tool_call_id: str = "call_001", content: str = "ok") -> Entry:
    """Helper to create a tool_result entry."""
    return Entry(
        kind="tool_result",
        payload={"tool_call_id": tool_call_id, "content": content},
    )


@dataclass
class FakePipelineContext:
    """Minimal stand-in for PipelineContext."""

    tape: Tape
    plugin_states: dict[str, Any] = field(default_factory=dict)


class TestSessionMetricsPluginStructure:
    """Plugin structure and registration."""

    def test_state_key(self) -> None:
        plugin = SessionMetricsPlugin()
        assert plugin.state_key == "session_metrics"

    def test_hooks_include_on_checkpoint(self) -> None:
        plugin = SessionMetricsPlugin()
        hooks = plugin.hooks()
        assert "on_checkpoint" in hooks

    def test_hooks_returns_callable(self) -> None:
        plugin = SessionMetricsPlugin()
        hooks = plugin.hooks()
        assert callable(hooks["on_checkpoint"])


class TestSessionMetricsStepsCount:
    """Steps count from tape tool_call entries."""

    def test_empty_tape_zero_steps(self) -> None:
        plugin = SessionMetricsPlugin()
        ctx = FakePipelineContext(tape=Tape())
        plugin.on_checkpoint(ctx=ctx)

        state = ctx.plugin_states["session_metrics"]
        assert state["steps_count"] == 0

    def test_tape_with_tool_calls_counts_steps(self) -> None:
        plugin = SessionMetricsPlugin()
        tape = Tape()
        tape.append(_make_tool_call("file_read", {"path": "/a.py"}))
        tape.append(_make_tool_result())
        tape.append(_make_tool_call("grep", {"pattern": "foo"}))
        tape.append(_make_tool_result())

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        state = ctx.plugin_states["session_metrics"]
        assert state["steps_count"] == 2

    def test_messages_not_counted_as_steps(self) -> None:
        plugin = SessionMetricsPlugin()
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "hello"})
        )
        tape.append(_make_tool_call("file_read"))
        tape.append(_make_tool_result())

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        state = ctx.plugin_states["session_metrics"]
        assert state["steps_count"] == 1


class TestSessionMetricsTurnTiming:
    """Turn timing tracking."""

    def test_turn_start_time_set_on_first_checkpoint(self) -> None:
        plugin = SessionMetricsPlugin()
        ctx = FakePipelineContext(tape=Tape())

        before = time.time()
        plugin.on_checkpoint(ctx=ctx)
        after = time.time()

        state = ctx.plugin_states["session_metrics"]
        assert before <= state["turn_start_time"] <= after

    def test_total_turn_time_positive_after_checkpoint(self) -> None:
        plugin = SessionMetricsPlugin()
        ctx = FakePipelineContext(tape=Tape())

        plugin.on_checkpoint(ctx=ctx)

        state = ctx.plugin_states["session_metrics"]
        assert state["total_turn_time"] >= 0.0

    def test_total_turn_time_increases_over_checkpoints(self) -> None:
        plugin = SessionMetricsPlugin()
        ctx = FakePipelineContext(tape=Tape())

        plugin.on_checkpoint(ctx=ctx)
        t1 = ctx.plugin_states["session_metrics"]["total_turn_time"]

        time.sleep(0.01)
        plugin.on_checkpoint(ctx=ctx)
        t2 = ctx.plugin_states["session_metrics"]["total_turn_time"]

        assert t2 > t1


class TestSessionMetricsToolCalls:
    """Per-tool call counting."""

    def test_tool_calls_per_name(self) -> None:
        plugin = SessionMetricsPlugin()
        tape = Tape()
        tape.append(_make_tool_call("file_read", {"path": "/a.py"}))
        tape.append(_make_tool_result())
        tape.append(_make_tool_call("file_read", {"path": "/b.py"}))
        tape.append(_make_tool_result())
        tape.append(_make_tool_call("grep", {"pattern": "x"}))
        tape.append(_make_tool_result())

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        state = ctx.plugin_states["session_metrics"]
        assert state["tool_calls"]["file_read"] == 2
        assert state["tool_calls"]["grep"] == 1

    def test_no_tool_calls_empty_dict(self) -> None:
        plugin = SessionMetricsPlugin()
        ctx = FakePipelineContext(tape=Tape())
        plugin.on_checkpoint(ctx=ctx)

        state = ctx.plugin_states["session_metrics"]
        assert state["tool_calls"] == {}


class TestSessionMetricsGetMetrics:
    """get_metrics() returns snapshot of current metrics."""

    def test_get_metrics_returns_dict(self) -> None:
        plugin = SessionMetricsPlugin()
        metrics = plugin.get_metrics()
        assert isinstance(metrics, dict)

    def test_get_metrics_has_expected_fields(self) -> None:
        plugin = SessionMetricsPlugin()
        tape = Tape()
        tape.append(_make_tool_call("file_read"))
        tape.append(_make_tool_result())

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        metrics = plugin.get_metrics()
        assert "steps_count" in metrics
        assert "tool_calls" in metrics
        assert "total_turn_time" in metrics
        assert "turn_start_time" in metrics
        assert "api_calls" in metrics
        assert "api_latency_total" in metrics

    def test_get_metrics_reflects_checkpoint_data(self) -> None:
        plugin = SessionMetricsPlugin()
        tape = Tape()
        tape.append(_make_tool_call("bash", {"command": "ls"}))
        tape.append(_make_tool_result())

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        metrics = plugin.get_metrics()
        assert metrics["steps_count"] == 1
        assert metrics["tool_calls"]["bash"] == 1


class TestSessionMetricsResetTurn:
    """reset_turn() clears per-turn state for next turn."""

    def test_reset_clears_steps(self) -> None:
        plugin = SessionMetricsPlugin()
        tape = Tape()
        tape.append(_make_tool_call("file_read"))
        tape.append(_make_tool_result())

        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        assert plugin.get_metrics()["steps_count"] == 1

        plugin.reset_turn()

        metrics = plugin.get_metrics()
        assert metrics["steps_count"] == 0
        assert metrics["tool_calls"] == {}

    def test_reset_clears_turn_timing(self) -> None:
        plugin = SessionMetricsPlugin()
        ctx = FakePipelineContext(tape=Tape())
        plugin.on_checkpoint(ctx=ctx)

        plugin.reset_turn()

        metrics = plugin.get_metrics()
        assert metrics["turn_start_time"] is None
        assert metrics["total_turn_time"] == 0.0

    def test_reset_preserves_api_totals(self) -> None:
        """API call totals are session-wide, not per-turn."""
        plugin = SessionMetricsPlugin()
        plugin.record_api_call(0.5)
        plugin.record_api_call(0.3)

        plugin.reset_turn()

        metrics = plugin.get_metrics()
        assert metrics["api_calls"] == 2
        assert metrics["api_latency_total"] == pytest.approx(0.8)


class TestSessionMetricsApiTracking:
    """API call and latency tracking (matches old SessionMetrics)."""

    def test_record_api_call(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.record_api_call(1.5)

        metrics = plugin.get_metrics()
        assert metrics["api_calls"] == 1
        assert metrics["api_latency_total"] == pytest.approx(1.5)

    def test_multiple_api_calls_accumulated(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.record_api_call(0.5)
        plugin.record_api_call(1.0)
        plugin.record_api_call(0.3)

        metrics = plugin.get_metrics()
        assert metrics["api_calls"] == 3
        assert metrics["api_latency_total"] == pytest.approx(1.8)

    def test_avg_api_latency(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.record_api_call(1.0)
        plugin.record_api_call(2.0)

        metrics = plugin.get_metrics()
        assert metrics["avg_api_latency"] == pytest.approx(1.5)

    def test_avg_api_latency_zero_when_no_calls(self) -> None:
        plugin = SessionMetricsPlugin()
        metrics = plugin.get_metrics()
        assert metrics["avg_api_latency"] == 0.0


class TestSessionMetricsNoCtx:
    """Edge case: on_checkpoint called without ctx."""

    def test_no_ctx_does_not_crash(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_checkpoint()  # Should not raise


class TestSessionMetricsTopicTracking:
    """P2: per-topic metrics via on_session_event."""

    def test_hooks_include_on_session_event(self) -> None:
        plugin = SessionMetricsPlugin()
        hooks = plugin.hooks()
        assert "on_session_event" in hooks

    def test_topic_start_sets_current_topic(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_session_event(
            event_type="topic_start", payload={"topic_id": "topic-abc"}
        )
        assert plugin._current_topic_id == "topic-abc"

    def test_topic_end_archives_metrics(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_session_event(
            event_type="topic_start", payload={"topic_id": "topic-abc"}
        )

        tape = Tape()
        tape.append(_make_tool_call("file_read", {"path": "/a.py"}))
        tape.append(_make_tool_result())
        tape.append(_make_tool_call("grep", {"pattern": "foo"}))
        tape.append(_make_tool_result())
        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        plugin.on_session_event(
            event_type="topic_end", payload={"topic_id": "topic-abc"}
        )

        topic_metrics = plugin.get_topic_metrics("topic-abc")
        assert topic_metrics is not None
        assert topic_metrics["steps_count"] == 2
        assert topic_metrics["tool_calls"]["file_read"] == 1
        assert topic_metrics["tool_calls"]["grep"] == 1

    def test_multiple_topics_tracked_independently(self) -> None:
        plugin = SessionMetricsPlugin()

        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "t1"})
        tape = Tape()
        tape.append(_make_tool_call("file_read"))
        tape.append(_make_tool_result())
        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)
        plugin.on_session_event(event_type="topic_end", payload={"topic_id": "t1"})

        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "t2"})
        tape2 = Tape()
        for _ in range(5):
            tape2.append(_make_tool_call("grep"))
            tape2.append(_make_tool_result())
        ctx2 = FakePipelineContext(tape=tape2)
        plugin.on_checkpoint(ctx=ctx2)
        plugin.on_session_event(event_type="topic_end", payload={"topic_id": "t2"})

        t1 = plugin.get_topic_metrics("t1")
        t2 = plugin.get_topic_metrics("t2")
        assert t1["steps_count"] == 1
        assert t2["steps_count"] == 5

    def test_get_all_topic_metrics(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "t1"})
        plugin.on_session_event(event_type="topic_end", payload={"topic_id": "t1"})

        all_metrics = plugin.get_all_topic_metrics()
        assert "t1" in all_metrics

    def test_unknown_topic_returns_none(self) -> None:
        plugin = SessionMetricsPlugin()
        assert plugin.get_topic_metrics("nonexistent") is None

    def test_non_topic_events_ignored(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_session_event(event_type="handoff", payload={"reason": "window"})
        assert plugin._current_topic_id is None
