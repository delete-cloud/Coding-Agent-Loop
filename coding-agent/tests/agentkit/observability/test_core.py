"""Tests for provider-neutral agentkit observability primitives."""

import pytest

from agentkit.observability import (
    NoopObservationSink,
    ObservationEvent,
    SpanRecord,
    record_span,
)


class InMemoryObservationSink:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []
        self.events: list[ObservationEvent] = []

    def record_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def record_event(self, event: ObservationEvent) -> None:
        self.events.append(event)


class FailingObservationSink:
    def record_span(self, span: SpanRecord) -> None:
        del span
        raise RuntimeError("sink unavailable")

    def record_event(self, event: ObservationEvent) -> None:
        del event
        raise RuntimeError("sink unavailable")


def test_record_span_records_success_with_copied_attributes() -> None:
    sink = InMemoryObservationSink()
    attributes = {"session_id": "session-1", "stage": "build_context"}

    with record_span(
        "runtime.stage.build_context",
        sink=sink,
        attributes=attributes,
    ) as span:
        attributes["stage"] = "mutated"
        span.set_attribute("turn_id", "turn-1")

    assert len(sink.spans) == 1
    recorded = sink.spans[0]
    assert recorded.name == "runtime.stage.build_context"
    assert recorded.status == "ok"
    assert recorded.attributes == {
        "session_id": "session-1",
        "stage": "build_context",
        "turn_id": "turn-1",
    }
    assert recorded.duration_ms >= 0


def test_record_span_records_exception_and_reraises() -> None:
    sink = InMemoryObservationSink()

    with pytest.raises(ValueError, match="boom"):
        with record_span("tool.call", sink=sink, attributes={"tool": "bash"}):
            raise ValueError("boom")

    assert len(sink.spans) == 1
    recorded = sink.spans[0]
    assert recorded.status == "error"
    assert recorded.error_type == "ValueError"
    assert recorded.error_message == "boom"
    assert recorded.attributes == {"tool": "bash"}


def test_record_span_truncates_long_error_message() -> None:
    sink = InMemoryObservationSink()
    long_message = "x" * 1000

    with pytest.raises(RuntimeError, match=r"x+"):
        with record_span("llm.generation", sink=sink):
            raise RuntimeError(long_message)

    assert len(sink.spans) == 1
    recorded = sink.spans[0]
    assert recorded.error_message is not None
    assert len(recorded.error_message) < len(long_message)
    assert recorded.error_message.endswith("...")


def test_record_span_sink_failure_does_not_fail_business_logic() -> None:
    with record_span(
        "runtime.stage.save_tape",
        sink=FailingObservationSink(),
        attributes={"session_id": "session-1"},
    ) as span:
        span.set_attribute("turn_id", "turn-1")


def test_noop_observation_sink_drops_spans_and_events() -> None:
    sink = NoopObservationSink()
    span = SpanRecord(name="runtime.stage.build_context", status="ok")
    event = ObservationEvent(name="runtime.stage.build_context.started")

    sink.record_span(span)
    sink.record_event(event)


def test_observation_event_copies_attributes() -> None:
    attributes = {"session_id": "session-1"}
    event = ObservationEvent(name="session.created", attributes=attributes)

    attributes["session_id"] = "mutated"

    assert event.attributes == {"session_id": "session-1"}
