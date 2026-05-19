from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Any

from agentkit.observability import (
    ActiveSpan,
    ObservationEvent,
    ObservationSink,
    record_span,
)


class ActionKind(StrEnum):
    FILE_EDIT = "file_edit"
    PATCH = "patch"
    COMMAND = "command"
    VALIDATION = "validation"
    RESTORE = "restore"


class ActionObservationStatus(StrEnum):
    STARTED = "started"
    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    COMPLETED = "completed"
    FAILED = "failed"


_SENSITIVE_ATTRIBUTE_PARTS = frozenset(
    {
        "content",
        "message",
        "prompt",
        "result",
        "secret",
        "text",
    }
)
_SAFE_LABEL = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
ACTION_OBSERVATION_NAME = "action_safety.action"


@dataclass(frozen=True)
class ActionObservation:
    kind: ActionKind
    status: ActionObservationStatus
    policy_decision: str | None = None
    policy_reasons: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str | None = None
    changed_path_count: int | None = None
    file_extension_buckets: tuple[str, ...] = field(default_factory=tuple)
    command_label: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    restore_status: str | None = None
    dry_run: bool | None = None

    def __post_init__(self) -> None:
        _validate_optional_label(self.policy_decision, "policy_decision")
        _validate_optional_label(self.risk_level, "risk_level")
        _validate_optional_label(self.command_label, "command_label")
        _validate_optional_label(self.restore_status, "restore_status")
        for reason in self.policy_reasons:
            _validate_label(reason, "policy_reasons")
        for bucket in self.file_extension_buckets:
            _validate_label(bucket, "file_extension_buckets")
        _validate_non_negative(self.changed_path_count, "changed_path_count")
        _validate_non_negative(self.duration_ms, "duration_ms")

    def to_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "action_kind": self.kind.value,
            "action_status": self.status.value,
        }
        _set_if_not_none(attributes, "policy_decision", self.policy_decision)
        if self.policy_reasons:
            attributes["policy_reason_count"] = len(self.policy_reasons)
            attributes["policy_reasons"] = ",".join(self.policy_reasons)
        _set_if_not_none(attributes, "risk_level", self.risk_level)
        _set_if_not_none(attributes, "changed_path_count", self.changed_path_count)
        if self.file_extension_buckets:
            attributes["file_extension_bucket_count"] = len(self.file_extension_buckets)
            attributes["file_extension_buckets"] = ",".join(self.file_extension_buckets)
        _set_if_not_none(attributes, "command_label", self.command_label)
        _set_if_not_none(attributes, "exit_code", self.exit_code)
        _set_if_not_none(attributes, "duration_ms", self.duration_ms)
        _set_if_not_none(attributes, "restore_status", self.restore_status)
        _set_if_not_none(attributes, "dry_run", self.dry_run)
        for key in attributes:
            _validate_attribute_key(key)
        return attributes


@dataclass(frozen=True)
class ActionSpanUpdater:
    span: ActiveSpan

    def set_observation(self, observation: ActionObservation) -> None:
        for key, value in observation.to_attributes().items():
            self.span.set_attribute(key, value)


def emit_action_event(sink: ObservationSink, observation: ActionObservation) -> None:
    try:
        sink.record_event(
            ObservationEvent(
                name=ACTION_OBSERVATION_NAME,
                attributes=observation.to_attributes(),
            )
        )
    except Exception:
        return


@contextmanager
def record_action_span(
    sink: ObservationSink,
    observation: ActionObservation,
) -> Iterator[ActionSpanUpdater]:
    with record_span(
        ACTION_OBSERVATION_NAME,
        sink=sink,
        attributes=observation.to_attributes(),
    ) as span:
        yield ActionSpanUpdater(span)


def _set_if_not_none(attributes: dict[str, Any], key: str, value: Any | None) -> None:
    if value is not None:
        attributes[key] = value


def _validate_attribute_key(key: str) -> None:
    folded = key.casefold()
    if any(part in folded for part in _SENSITIVE_ATTRIBUTE_PARTS):
        raise ValueError(f"unsafe action observation attribute key: {key}")


def _validate_optional_label(value: str | None, field_name: str) -> None:
    if value is None:
        return
    _validate_label(value, field_name)


def _validate_label(value: str, field_name: str) -> None:
    if _SAFE_LABEL.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a bounded safe label")


def _validate_non_negative(value: int | None, field_name: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be non-negative")
