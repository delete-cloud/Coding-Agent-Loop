from __future__ import annotations

import json

import pytest

from agentkit.observability import ObservationEvent, SpanRecord
from coding_agent.action_safety import (
    ACTION_OBSERVATION_NAME,
    ActionKind,
    ActionObservation,
    ActionObservationStatus,
    emit_action_event,
    record_action_span,
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
        raise RuntimeError("span sink failed")

    def record_event(self, event: ObservationEvent) -> None:
        del event
        raise RuntimeError("event sink failed")


def test_action_observability_emits_safe_metadata_without_sensitive_attributes() -> (
    None
):
    sink = InMemoryObservationSink()
    observation = ActionObservation(
        kind=ActionKind.VALIDATION,
        status=ActionObservationStatus.FAILED,
        policy_decision="allow",
        policy_reasons=("validation_command",),
        risk_level="low",
        changed_path_count=2,
        file_extension_buckets=("py", "md"),
        command_label="unit-tests",
        exit_code=3,
        duration_ms=17,
        dry_run=True,
    )

    emit_action_event(sink, observation)
    with record_action_span(sink, observation):
        pass

    assert len(sink.events) == 1
    assert len(sink.spans) == 1
    assert sink.events[0].name == ACTION_OBSERVATION_NAME
    assert sink.spans[0].name == ACTION_OBSERVATION_NAME
    event_attributes = sink.events[0].attributes
    span_attributes = sink.spans[0].attributes
    assert event_attributes == span_attributes
    assert event_attributes["action_kind"] == "validation"
    assert event_attributes["action_status"] == "failed"
    assert event_attributes["policy_decision"] == "allow"
    assert event_attributes["policy_reasons"] == "validation_command"
    assert event_attributes["policy_reason_count"] == 1
    assert event_attributes["file_extension_buckets"] == "py,md"
    assert event_attributes["command_label"] == "unit-tests"
    assert event_attributes["exit_code"] == 3
    assert event_attributes["dry_run"] is True

    serialized = json.dumps({"event": event_attributes, "span": span_attributes})
    forbidden_parts = {"content", "message", "prompt", "result", "secret", "text"}
    assert all(part not in key for key in event_attributes for part in forbidden_parts)
    assert "SECRET_VALUE" not in serialized
    assert "raw command output" not in serialized


def test_action_observability_event_sink_failure_does_not_fail_business_logic() -> None:
    emit_action_event(
        FailingObservationSink(),
        ActionObservation(
            kind=ActionKind.COMMAND,
            status=ActionObservationStatus.STARTED,
        ),
    )


def test_action_observability_span_allows_safe_final_metadata_updates() -> None:
    sink = InMemoryObservationSink()
    started = ActionObservation(
        kind=ActionKind.VALIDATION,
        status=ActionObservationStatus.STARTED,
    )
    completed = ActionObservation(
        kind=ActionKind.VALIDATION,
        status=ActionObservationStatus.COMPLETED,
        exit_code=0,
        duration_ms=9,
    )

    with record_action_span(sink, started) as action_span:
        action_span.set_observation(completed)

    assert sink.spans[0].attributes["action_status"] == "completed"
    assert sink.spans[0].attributes["exit_code"] == 0
    assert sink.spans[0].attributes["duration_ms"] == 9


def test_action_observability_span_rejects_unsafe_final_metadata() -> None:
    sink = InMemoryObservationSink()
    started = ActionObservation(
        kind=ActionKind.VALIDATION,
        status=ActionObservationStatus.STARTED,
    )

    with pytest.raises(ValueError, match="command_label"):
        with record_action_span(sink, started) as action_span:
            action_span.set_observation(
                ActionObservation(
                    kind=ActionKind.COMMAND,
                    status=ActionObservationStatus.FAILED,
                    command_label="python -c 'print(SECRET_VALUE)'",
                )
            )


def test_action_observability_rejects_unbounded_string_values() -> None:
    with pytest.raises(ValueError, match="command_label"):
        _ = ActionObservation(
            kind=ActionKind.COMMAND,
            status=ActionObservationStatus.STARTED,
            command_label="python -c 'print(SECRET_VALUE)'",
        )

    with pytest.raises(ValueError, match="policy_reasons"):
        _ = ActionObservation(
            kind=ActionKind.COMMAND,
            status=ActionObservationStatus.DENIED,
            policy_reasons=("contains raw command output",),
        )


def test_action_observability_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="changed_path_count"):
        _ = ActionObservation(
            kind=ActionKind.PATCH,
            status=ActionObservationStatus.COMPLETED,
            changed_path_count=-1,
        )
