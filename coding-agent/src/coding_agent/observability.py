"""Coding Agent observability configuration and sink construction."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Final

import httpx

from agentkit.observability import (
    NoopObservationSink,
    ObservationEvent,
    ObservationSink,
    SpanRecord,
)

_SENSITIVE_ATTRIBUTE_PARTS = frozenset({
    "content",
    "env",
    "message",
    "output",
    "prompt",
    "result",
    "secret",
    "stderr",
    "stdout",
    "text",
})
_SANITIZED_LANGFUSE_OBSERVATION_KEYS = frozenset({
    "langfuse.observation.input",
    "langfuse.observation.output",
})
_SANITIZED_LANGFUSE_ALLOWED_KEYS = frozenset({
    "arg_shape",
    "field_count",
    "final_length",
    "final_present",
    "input",
    "item_count",
    "key_count",
    "length",
    "line_count",
    "list",
    "output",
    "result_shape",
    "safe_keys",
    "status",
    "string_lengths",
    "tool_call_count",
    "tool_calls",
    "tool_name",
    "type",
    "types",
    "user_length",
    "user_present",
})
_SENSITIVE_PROMETHEUS_VALUE_PARTS = frozenset({
    "content",
    "env",
    "key",
    "log",
    "message",
    "output",
    "password",
    "private",
    "prompt",
    "raw",
    "result",
    "secret",
    "stderr",
    "stdout",
    "text",
    "token",
})
_FORBIDDEN_PROMETHEUS_LABELS = frozenset({
    "run_id",
    "schedule_id",
    "session_id",
    "signal_id",
    "template_id",
    "node_id",
    "task_id",
    "trace_id",
    "topic_id",
    "event_id",
    "executor_id",
    "executor_run_id",
    "interaction_id",
    "job_name",
    "launch_id",
    "memory_id",
    "pod_name",
    "tool_call_id",
    "workflow_name",
    "file_path",
    "prompt",
    "message",
    "content",
    "command_output",
    "secret",
})
_PROMETHEUS_ALLOWED_ATTRIBUTE_LABELS = frozenset({
    "action_kind",
    "action_status",
    "cache_hit",
    "error_type",
    "executor_kind",
    "eval_status",
    "hitl_status",
    "memory_kind",
    "memory_review_status",
    "memory_status",
    "model",
    "operation",
    "policy_decision",
    "provider",
    "recall_source",
    "recall_status",
    "risk_level",
    "schedule_kind",
    "schedule_status",
    "signal_kind",
    "signal_status",
    "source_kind",
    "stage",
    "status",
    "storage_status",
    "task_kind",
    "task_profile",
    "task_status",
    "template_kind",
    "template_profile",
    "command_category",
    "command_policy",
    "command_status",
    "tool_name",
    "node_kind",
    "node_profile",
    "node_status",
    "topic_kind",
    "topic_profile",
    "topic_status",
    "trigger_kind",
})
_PROMETHEUS_ATTRIBUTE_LABEL_ALIASES = {
    "retrieval.cache_hit": "cache_hit",
    "retrieval.source_kind": "source_kind",
}
_PROMETHEUS_RESERVED_LABELS = frozenset({"event", "span", "status"})
_PROMETHEUS_KNOWN_SPAN_NAMES = frozenset({
    "action.execute",
    "action_safety.action",
    "context_pack.build",
    "context_pack.render",
    "kb.index_failure",
    "kb.index_repo",
    "kb.query",
    "llm.generation",
    "runtime.stage.apply_directives",
    "runtime.stage.build_context",
    "runtime.stage.dispatch",
    "runtime.stage.load_state",
    "runtime.stage.render",
    "runtime.stage.run_model",
    "runtime.stage.save_state",
    "tool.call",
    "retrieval.kb.search",
})
_PROMETHEUS_KNOWN_EVENT_NAMES = frozenset({
    "action_safety.action",
    "action.completed",
    "action.failed",
    "action.started",
    "runtime.started",
    "tool.call.completed",
})
_PROMETHEUS_LABEL_VALUE_ALLOWLISTS = {
    "action_kind": frozenset({
        "approval",
        "command",
        "command_policy",
        "file_edit",
        "patch",
        "restore",
        "validation",
    }),
    "action_status": frozenset({
        "allowed",
        "approval_required",
        "completed",
        "denied",
        "failed",
        "started",
    }),
    "cache_hit": frozenset({"false", "true"}),
    "executor_kind": frozenset({
        "local",
        "docker",
        "kubernetes_job",
        "argo_workflow",
        "fixture",
        "unknown",
    }),
    "eval_status": frozenset({"failed", "passed", "skipped"}),
    "hitl_status": frozenset({"approved", "rejected", "requested", "timed_out"}),
    "memory_kind": frozenset({
        "command_memory",
        "decision",
        "fact",
        "incident",
        "procedure",
        "project_convention",
        "unknown",
    }),
    "memory_review_status": frozenset({"accepted", "archived", "rejected"}),
    "memory_status": frozenset({"accepted", "archived", "candidate", "rejected"}),
    "operation": frozenset({
        "checkpoint_load",
        "checkpoint_save",
        "session_load",
        "session_save",
        "tape_append",
        "tape_load",
    }),
    "policy_decision": frozenset({"allow", "approval_required", "deny"}),
    "recall_source": frozenset({
        "accepted_memory",
        "none",
        "topic_and_memory",
        "topic_range",
        "unknown",
    }),
    "recall_status": frozenset({
        "disabled",
        "empty",
        "failed",
        "matched",
        "unknown",
    }),
    "risk_level": frozenset({"low", "medium", "high"}),
    "schedule_kind": frozenset({"interval", "manual", "once", "unknown"}),
    "schedule_status": frozenset({"active", "completed", "disabled", "paused"}),
    "signal_kind": frozenset({"repo_activity", "unknown"}),
    "signal_status": frozenset({"consumed", "ignored", "new", "planned"}),
    "source_kind": frozenset({"kb", "kb_chunk", "repo_file", "test_failure"}),
    "stage": frozenset({
        "apply_directives",
        "build_context",
        "dispatch",
        "load_state",
        "render",
        "run_model",
        "save_state",
    }),
    "status": frozenset({"ok", "error", "started", "completed", "failed"}),
    "storage_status": frozenset({"ok", "error"}),
    "task_kind": frozenset({"maintenance", "unknown"}),
    "task_profile": frozenset({"ci", "demo", "local", "unknown"}),
    "task_status": frozenset({
        "pending",
        "running",
        "completed",
        "failed",
        "cancelled",
    }),
    "template_kind": frozenset({"maintenance", "unknown"}),
    "template_profile": frozenset({"ci", "demo", "local", "unknown"}),
    "command_category": frozenset({"analysis", "report", "validation", "unknown"}),
    "command_policy": frozenset({"existing_command_policy", "unknown"}),
    "command_status": frozenset({"declared", "disabled", "unknown"}),
    "node_kind": frozenset({"analysis", "report", "validation", "unknown"}),
    "node_profile": frozenset({"ci", "demo", "default", "local", "unknown"}),
    "node_status": frozenset({
        "pending",
        "ready",
        "running",
        "completed",
        "failed",
        "skipped",
    }),
    "topic_kind": frozenset({"coding", "unknown"}),
    "topic_profile": frozenset({"ci", "demo", "local", "unknown"}),
    "topic_status": frozenset({"open", "finalized", "aborted"}),
    "trigger_kind": frozenset({"proactive_signal", "schedule", "unknown"}),
}
_BEE_LAUNCH_SOURCES: Final[frozenset[str]] = frozenset({
    "manual",
    "schedule",
    "proactive_signal",
})
_BEE_LAUNCH_STATUSES: Final[frozenset[str]] = frozenset({
    "planned",
    "launching",
    "launched",
    "failed",
    "cancelled",
})
_BEE_PROACTIVE_SIGNAL_KINDS: Final[frozenset[str]] = frozenset({
    "repo_activity",
    "unknown",
})
_EXECUTOR_KINDS: Final[frozenset[str]] = frozenset({
    "local",
    "docker",
    "kubernetes_job",
    "argo_workflow",
    "fixture",
    "unknown",
})
_EXECUTOR_STATUSES: Final[frozenset[str]] = frozenset({
    "available",
    "cancelled",
    "disabled",
    "failed",
    "planned",
    "running",
    "submitted",
    "succeeded",
    "unavailable",
    "unknown",
})
_TOPIC_RECALL_SOURCES: Final[frozenset[str]] = frozenset({
    "accepted_memory",
    "none",
    "topic_and_memory",
    "topic_range",
    "unknown",
})
_TOPIC_RECALL_STATUSES: Final[frozenset[str]] = frozenset({
    "disabled",
    "empty",
    "failed",
    "matched",
    "unknown",
})
_MEMORY_KINDS: Final[frozenset[str]] = frozenset({
    "command_memory",
    "decision",
    "fact",
    "incident",
    "procedure",
    "project_convention",
    "unknown",
})
_MEMORY_STATUSES: Final[frozenset[str]] = frozenset({
    "accepted",
    "archived",
    "candidate",
    "rejected",
})
_MEMORY_REVIEW_STATUSES: Final[frozenset[str]] = frozenset({
    "accepted",
    "archived",
    "rejected",
})
_BEE_PACK_SOURCE_TYPES: Final[frozenset[str]] = frozenset({
    "fixture",
    "imported",
    "local_workspace",
})
_BEE_PACK_COMPATIBILITY_STATUSES: Final[frozenset[str]] = frozenset({
    "compatible",
    "incompatible",
    "warning",
})
_BEE_PACK_DRY_RUN_STATUSES: Final[frozenset[str]] = frozenset({
    "ready",
    "rejected",
    "warning",
})
_PROMETHEUS_HISTOGRAM_BUCKETS = (5.0, 10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 5000.0)
_PROMETHEUS_HTTP_HISTOGRAM_BUCKETS = (
    1.0,
    5.0,
    10.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    5000.0,
)
_PROMETHEUS_HTTP_METHODS = frozenset({
    "CONNECT",
    "DELETE",
    "GET",
    "HEAD",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
    "TRACE",
})
_LABEL_VALUE_PATTERN = re.compile(r"[^a-zA-Z0-9_.:-]+")


def _otlp_endpoint(endpoint: str) -> str:
    stripped = endpoint.rstrip("/")
    if stripped.endswith("/v1/traces"):
        return stripped
    return f"{stripped}/v1/traces"


def _attribute_allowed(key: str) -> bool:
    key_folded = key.casefold()
    return not any(part in key_folded for part in _SENSITIVE_ATTRIBUTE_PARTS)


def _prometheus_label_allowed(key: str) -> bool:
    key = _PROMETHEUS_ATTRIBUTE_LABEL_ALIASES.get(key, key)
    key_folded = key.casefold()
    if key_folded in _FORBIDDEN_PROMETHEUS_LABELS:
        return False
    if not _attribute_allowed(key):
        return False
    return key in _PROMETHEUS_ALLOWED_ATTRIBUTE_LABELS


def _prometheus_label_value(value: Any) -> str:
    if isinstance(value, bool):
        raw = "true" if value else "false"
    elif isinstance(value, int | float) and not isinstance(value, bool):
        raw = str(value)
    else:
        raw = str(value)
    normalized = _LABEL_VALUE_PATTERN.sub("_", raw.strip())[:80].strip("_")
    return normalized or "unknown"


def _prometheus_allowed_label_value(key: str, value: Any) -> str:
    normalized = _prometheus_label_value(value)
    normalized_tokens = {
        token for token in re.split(r"[_.:-]+", normalized.casefold()) if token
    }
    if normalized_tokens & _SENSITIVE_PROMETHEUS_VALUE_PARTS:
        return "unknown"
    allowed_values = _PROMETHEUS_LABEL_VALUE_ALLOWLISTS.get(key)
    if allowed_values is not None and normalized not in allowed_values:
        return "unknown"
    return normalized


def _prometheus_metric_part(value: str, allowed_values: frozenset[str]) -> str:
    normalized = _LABEL_VALUE_PATTERN.sub("_", value.strip())[:80].strip("_")
    if normalized not in allowed_values:
        return "unknown"
    return normalized or "unknown"


def _prometheus_http_method(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in _PROMETHEUS_HTTP_METHODS:
        return "unknown"
    return normalized


def _prometheus_http_route(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("/"):
        return "unknown"
    if any(part in normalized.casefold() for part in _FORBIDDEN_PROMETHEUS_LABELS):
        return "unknown"
    return _LABEL_VALUE_PATTERN.sub("_", normalized[:120]).strip("_") or "root"


def _prometheus_http_status(value: int) -> str:
    if 100 <= value <= 599:
        return str(value)
    return "unknown"


def _prometheus_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _prometheus_labels(
    attributes: Mapping[str, Any],
    *,
    reserved: frozenset[str] = frozenset(),
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for key, value in attributes.items():
        label_key = _PROMETHEUS_ATTRIBUTE_LABEL_ALIASES.get(key, key)
        if label_key in reserved:
            continue
        if not _prometheus_label_allowed(key):
            continue
        if isinstance(value, bool | int | float | str):
            labels[label_key] = _prometheus_allowed_label_value(label_key, value)
    return labels


def _otlp_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"intValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _otlp_attributes(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _otlp_value(value)}
        for key, value in attributes.items()
        if _otlp_attribute_allowed(key, value)
    ]


def _otlp_attribute_allowed(key: str, value: Any) -> bool:
    if key in _SANITIZED_LANGFUSE_OBSERVATION_KEYS:
        return _sanitized_langfuse_attribute_allowed(key, value)
    return _attribute_allowed(key) and _attribute_value_allowed(value)


def _attribute_value_allowed(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    normalized_tokens = {
        token for token in re.split(r"[^a-zA-Z0-9]+", value.casefold()) if token
    }
    return not bool(normalized_tokens & _SENSITIVE_PROMETHEUS_VALUE_PARTS)


def _sanitized_langfuse_attribute_allowed(key: str, value: Any) -> bool:
    if key not in _SANITIZED_LANGFUSE_OBSERVATION_KEYS:
        return False
    if not isinstance(value, str):
        return False
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return False
    return _sanitized_langfuse_payload_allowed(payload)


def _sanitized_langfuse_payload_allowed(value: Any) -> bool:
    if isinstance(value, bool | int | float) or value is None:
        return True
    if isinstance(value, str):
        return _attribute_value_allowed(value)
    if isinstance(value, list):
        return all(_sanitized_langfuse_payload_allowed(item) for item in value)
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return False
            if key not in _SANITIZED_LANGFUSE_ALLOWED_KEYS:
                return False
            if not _sanitized_langfuse_payload_allowed(item):
                return False
        return True
    return False


def _resource_attributes(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    resource: dict[str, Any] = {"service.name": "coding-agent"}
    session_id = attributes.get("session_id")
    if isinstance(session_id, str) and session_id:
        resource["session.id"] = session_id
    run_id = attributes.get("run_id")
    if isinstance(run_id, str) and run_id:
        resource["run.id"] = run_id
    return _otlp_attributes(resource)


def _trace_id(attributes: Mapping[str, Any]) -> str:
    session_id = attributes.get("session_id")
    run_id = attributes.get("run_id")
    if (
        isinstance(session_id, str)
        and session_id
        and isinstance(run_id, str)
        and run_id
    ):
        return hashlib.sha256(f"{session_id}:{run_id}".encode("utf-8")).hexdigest()[:32]
    return secrets.token_hex(16)


def _nanos(timestamp: float | None) -> str:
    if timestamp is None:
        return "0"
    return str(int(timestamp * 1_000_000_000))


@dataclass
class CompositeObservationSink:
    """Observation sink that fans out to multiple child sinks."""

    sinks: tuple[ObservationSink, ...]

    def __post_init__(self) -> None:
        if not self.sinks:
            raise ValueError("CompositeObservationSink requires at least one sink")
        for sink in self.sinks:
            if not isinstance(sink, ObservationSink):
                raise TypeError(
                    "all composite observation sinks must implement ObservationSink"
                )

    def record_span(self, span: SpanRecord) -> None:
        for sink in self.sinks:
            try:
                sink.record_span(span)
            except Exception:
                continue

    def record_event(self, event: ObservationEvent) -> None:
        for sink in self.sinks:
            try:
                sink.record_event(event)
            except Exception:
                continue


@dataclass
class PrometheusMetricsObservationSink:
    """Observation sink that records low-cardinality Prometheus metrics."""

    recorder: "PrometheusMetricsRecorder" = field(
        default_factory=lambda: _DEFAULT_PROMETHEUS_RECORDER
    )

    def record_span(self, span: SpanRecord) -> None:
        try:
            self.recorder.record_span(span)
        except Exception:
            return

    def record_event(self, event: ObservationEvent) -> None:
        try:
            self.recorder.record_event(event)
        except Exception:
            return


@dataclass
class PrometheusMetricsRecorder:
    """Small deterministic Prometheus registry for Coding Agent observations."""

    _counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        default_factory=dict
    )
    _gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        default_factory=dict
    )
    _histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(
        default_factory=dict
    )
    _lock: RLock = field(default_factory=RLock)

    def record_span(self, span: SpanRecord) -> None:
        labels = {
            "span": _prometheus_metric_part(span.name, _PROMETHEUS_KNOWN_SPAN_NAMES),
            "status": _prometheus_label_value(span.status),
        }
        labels.update(
            _prometheus_labels(span.attributes, reserved=_PROMETHEUS_RESERVED_LABELS)
        )
        if span.error_type:
            labels["error_type"] = _prometheus_allowed_label_value(
                "error_type",
                span.error_type,
            )
        with self._lock:
            self._inc("coding_agent_observation_spans_total", labels)
            self._observe(
                "coding_agent_observation_span_duration_ms",
                labels,
                max(0.0, float(span.duration_ms)),
            )

    def record_event(self, event: ObservationEvent) -> None:
        labels = {
            "event": _prometheus_metric_part(event.name, _PROMETHEUS_KNOWN_EVENT_NAMES)
        }
        labels.update(
            _prometheus_labels(event.attributes, reserved=frozenset({"event"}))
        )
        with self._lock:
            self._inc("coding_agent_observation_events_total", labels)
            self._set(
                "coding_agent_observation_last_event_timestamp_seconds",
                labels,
                float(event.timestamp),
            )

    def record_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        labels = {
            "method": _prometheus_http_method(method),
            "route": _prometheus_http_route(route),
            "status_code": _prometheus_http_status(status_code),
        }
        with self._lock:
            self._inc("coding_agent_http_requests_total", labels)
            self._observe(
                "coding_agent_http_request_duration_ms",
                labels,
                max(0.0, float(duration_ms)),
            )

    def record_evaluation_case_result(self, *, status: str) -> None:
        labels = {"eval_status": _prometheus_allowed_label_value("eval_status", status)}
        with self._lock:
            self._inc("coding_agent_evaluation_case_results_total", labels)

    def record_hitl_interaction(self, *, status: str) -> None:
        labels = {"hitl_status": _prometheus_allowed_label_value("hitl_status", status)}
        with self._lock:
            self._inc("coding_agent_hitl_interactions_total", labels)

    def record_storage_operation(
        self,
        *,
        operation: str,
        status: str,
        duration_ms: float,
    ) -> None:
        labels = {
            "operation": _prometheus_allowed_label_value("operation", operation),
            "storage_status": _prometheus_allowed_label_value("storage_status", status),
        }
        with self._lock:
            self._inc("coding_agent_storage_operations_total", labels)
            self._observe(
                "coding_agent_storage_operation_duration_ms",
                labels,
                max(0.0, float(duration_ms)),
            )

    def record_bee_launch(
        self,
        *,
        source: str,
        status: str,
        duration_ms: float,
        proactive_kind: str = "unknown",
    ) -> None:
        labels = {
            "source": _prometheus_metric_part(source, _BEE_LAUNCH_SOURCES),
            "status": _prometheus_metric_part(status, _BEE_LAUNCH_STATUSES),
        }
        with self._lock:
            self._inc("bee_launches_total", labels)
            self._observe(
                "bee_launch_duration_seconds",
                labels,
                max(0.0, float(duration_ms)) / 1000.0,
            )
            if source == "schedule":
                self._inc("scheduled_bee_launches_total", {"status": labels["status"]})
            elif source == "proactive_signal":
                self._inc(
                    "proactive_bee_launches_total",
                    {
                        "kind": _prometheus_metric_part(
                            proactive_kind,
                            _BEE_PROACTIVE_SIGNAL_KINDS,
                        ),
                        "status": labels["status"],
                    },
                )

    def record_executor_run(
        self,
        *,
        executor_kind: str,
        status: str,
        duration_ms: float,
    ) -> None:
        labels = {
            "executor_kind": _prometheus_metric_part(executor_kind, _EXECUTOR_KINDS),
            "status": _prometheus_metric_part(status, _EXECUTOR_STATUSES),
        }
        with self._lock:
            self._inc("executor_runs_total", labels)
            self._observe(
                "executor_run_duration_seconds",
                labels,
                max(0.0, float(duration_ms)) / 1000.0,
            )

    def record_executor_capability(
        self,
        *,
        executor_kind: str,
        status: str,
    ) -> None:
        labels = {
            "executor_kind": _prometheus_metric_part(executor_kind, _EXECUTOR_KINDS),
            "status": _prometheus_metric_part(status, _EXECUTOR_STATUSES),
        }
        with self._lock:
            self._set("executor_capability_status", labels, 1.0)

    def record_topic_recall_run(
        self,
        *,
        source: str,
        status: str,
        candidate_count: int,
    ) -> None:
        labels = {
            "source": _prometheus_metric_part(source, _TOPIC_RECALL_SOURCES),
            "status": _prometheus_metric_part(status, _TOPIC_RECALL_STATUSES),
        }
        histogram_labels = {"source": labels["source"]}
        with self._lock:
            self._inc("topic_recall_runs_total", labels)
            self._observe(
                "topic_recall_candidates",
                histogram_labels,
                max(0.0, float(candidate_count)),
            )

    def record_memory_candidate(self, *, kind: str, status: str) -> None:
        labels = {
            "kind": _prometheus_metric_part(kind, _MEMORY_KINDS),
            "status": _prometheus_metric_part(status, _MEMORY_STATUSES),
        }
        with self._lock:
            self._inc("memory_candidates_total", labels)

    def record_memory_review(self, *, status: str) -> None:
        labels = {"status": _prometheus_metric_part(status, _MEMORY_REVIEW_STATUSES)}
        with self._lock:
            self._inc("memory_reviews_total", labels)

    def record_bee_pack_validation(self, *, status: str, source_type: str) -> None:
        labels = {
            "source_type": _prometheus_metric_part(
                source_type,
                _BEE_PACK_SOURCE_TYPES,
            ),
            "status": _prometheus_metric_part(
                status,
                _BEE_PACK_COMPATIBILITY_STATUSES,
            ),
        }
        with self._lock:
            self._inc("bee_pack_validations_total", labels)

    def record_bee_pack_template(self, *, status: str, source_type: str) -> None:
        labels = {
            "source_type": _prometheus_metric_part(
                source_type,
                _BEE_PACK_SOURCE_TYPES,
            ),
            "status": _prometheus_metric_part(
                status,
                _BEE_PACK_COMPATIBILITY_STATUSES,
            ),
        }
        with self._lock:
            self._inc("bee_pack_templates_total", labels)

    def record_bee_pack_dry_run(self, *, status: str) -> None:
        labels = {"status": _prometheus_metric_part(status, _BEE_PACK_DRY_RUN_STATUSES)}
        with self._lock:
            self._inc("bee_pack_dry_runs_total", labels)

    def exposition_text(self) -> str:
        lines: list[str] = []
        with self._lock:
            span_counters = self._span_counters
            event_counters = self._event_counters
            if span_counters:
                lines.extend((
                    "# HELP coding_agent_observation_spans_total Observation spans recorded.",
                    "# TYPE coding_agent_observation_spans_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "coding_agent_observation_spans_total", span_counters
                    )
                )
            observation_histograms = self._observation_span_histograms
            if observation_histograms:
                lines.extend((
                    "# HELP coding_agent_observation_span_duration_ms Observation span duration in milliseconds.",
                    "# TYPE coding_agent_observation_span_duration_ms histogram",
                ))
                lines.extend(self._format_histograms(histograms=observation_histograms))
            if event_counters:
                lines.extend((
                    "# HELP coding_agent_observation_events_total Observation events recorded.",
                    "# TYPE coding_agent_observation_events_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "coding_agent_observation_events_total",
                        event_counters,
                    )
                )
            if self._gauges:
                lines.extend((
                    "# HELP coding_agent_observation_last_event_timestamp_seconds Last observation event timestamp.",
                    "# TYPE coding_agent_observation_last_event_timestamp_seconds gauge",
                ))
                lines.extend(
                    self._format_metric(
                        "coding_agent_observation_last_event_timestamp_seconds",
                        self._gauges,
                    )
                )
            http_counters = self._http_request_counters
            http_histograms = self._http_request_histograms
            if http_counters:
                lines.extend((
                    "# HELP coding_agent_http_requests_total HTTP requests handled.",
                    "# TYPE coding_agent_http_requests_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "coding_agent_http_requests_total",
                        http_counters,
                    )
                )
            if http_histograms:
                lines.extend((
                    "# HELP coding_agent_http_request_duration_ms HTTP request duration in milliseconds.",
                    "# TYPE coding_agent_http_request_duration_ms histogram",
                ))
                lines.extend(
                    self._format_histograms(
                        histograms=http_histograms,
                        metric_name="coding_agent_http_request_duration_ms",
                        buckets=_PROMETHEUS_HTTP_HISTOGRAM_BUCKETS,
                    )
                )
            domain_counters = self._domain_counters
            if domain_counters:
                lines.extend((
                    "# HELP coding_agent_evaluation_case_results_total Evaluation case results.",
                    "# TYPE coding_agent_evaluation_case_results_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "coding_agent_evaluation_case_results_total",
                        domain_counters,
                    )
                )
                lines.extend((
                    "# HELP coding_agent_hitl_interactions_total Human interaction outcomes.",
                    "# TYPE coding_agent_hitl_interactions_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "coding_agent_hitl_interactions_total",
                        domain_counters,
                    )
                )
                lines.extend((
                    "# HELP coding_agent_storage_operations_total Storage operations.",
                    "# TYPE coding_agent_storage_operations_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "coding_agent_storage_operations_total",
                        domain_counters,
                    )
                )
            storage_histograms = self._storage_operation_histograms
            if storage_histograms:
                lines.extend((
                    "# HELP coding_agent_storage_operation_duration_ms Storage operation duration in milliseconds.",
                    "# TYPE coding_agent_storage_operation_duration_ms histogram",
                ))
                lines.extend(
                    self._format_histograms(
                        histograms=storage_histograms,
                        metric_name="coding_agent_storage_operation_duration_ms",
                        buckets=_PROMETHEUS_HISTOGRAM_BUCKETS,
                    )
                )
            bee_launch_counters = self._bee_launch_counters
            bee_launch_histograms = self._bee_launch_histograms
            if bee_launch_counters:
                lines.extend((
                    "# HELP bee_launches_total Bee launches by source and status.",
                    "# TYPE bee_launches_total counter",
                ))
                lines.extend(
                    self._format_metric("bee_launches_total", bee_launch_counters)
                )
                lines.extend((
                    "# HELP scheduled_bee_launches_total Scheduled Bee launches.",
                    "# TYPE scheduled_bee_launches_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "scheduled_bee_launches_total",
                        bee_launch_counters,
                    )
                )
                lines.extend((
                    "# HELP proactive_bee_launches_total Proactive Bee launches.",
                    "# TYPE proactive_bee_launches_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "proactive_bee_launches_total",
                        bee_launch_counters,
                    )
                )
            if bee_launch_histograms:
                lines.extend((
                    "# HELP bee_launch_duration_seconds Bee launch duration in seconds.",
                    "# TYPE bee_launch_duration_seconds histogram",
                ))
                lines.extend(
                    self._format_histograms(
                        histograms=bee_launch_histograms,
                        metric_name="bee_launch_duration_seconds",
                        buckets=_PROMETHEUS_HISTOGRAM_BUCKETS,
                    )
                )
            executor_counters = self._executor_counters
            executor_histograms = self._executor_histograms
            executor_capabilities = self._executor_capability_gauges
            if executor_counters:
                lines.extend((
                    "# HELP executor_runs_total External executor runs by kind and status.",
                    "# TYPE executor_runs_total counter",
                ))
                lines.extend(
                    self._format_metric("executor_runs_total", executor_counters)
                )
            if executor_histograms:
                lines.extend((
                    "# HELP executor_run_duration_seconds External executor run duration in seconds.",
                    "# TYPE executor_run_duration_seconds histogram",
                ))
                lines.extend(
                    self._format_histograms(
                        histograms=executor_histograms,
                        metric_name="executor_run_duration_seconds",
                        buckets=_PROMETHEUS_HISTOGRAM_BUCKETS,
                    )
                )
            if executor_capabilities:
                lines.extend((
                    "# HELP executor_capability_status External executor capability status.",
                    "# TYPE executor_capability_status gauge",
                ))
                lines.extend(
                    self._format_metric(
                        "executor_capability_status",
                        executor_capabilities,
                    )
                )
            topic_recall_counters = self._topic_recall_counters
            topic_recall_histograms = self._topic_recall_histograms
            if topic_recall_counters:
                lines.extend((
                    "# HELP topic_recall_runs_total Topic recall runs by source and status.",
                    "# TYPE topic_recall_runs_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "topic_recall_runs_total",
                        topic_recall_counters,
                    )
                )
            if topic_recall_histograms:
                lines.extend((
                    "# HELP topic_recall_candidates Recall candidates returned by source.",
                    "# TYPE topic_recall_candidates histogram",
                ))
                lines.extend(
                    self._format_histograms(
                        histograms=topic_recall_histograms,
                        metric_name="topic_recall_candidates",
                        buckets=_PROMETHEUS_HISTOGRAM_BUCKETS,
                    )
                )
            memory_counters = self._memory_counters
            if memory_counters:
                lines.extend((
                    "# HELP memory_candidates_total Topic-derived memory candidates by kind and status.",
                    "# TYPE memory_candidates_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "memory_candidates_total",
                        memory_counters,
                    )
                )
                lines.extend((
                    "# HELP memory_reviews_total Memory review transitions by status.",
                    "# TYPE memory_reviews_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "memory_reviews_total",
                        memory_counters,
                    )
                )
            bee_pack_counters = self._bee_pack_counters
            if bee_pack_counters:
                lines.extend((
                    "# HELP bee_pack_validations_total Bee template pack compatibility validations.",
                    "# TYPE bee_pack_validations_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "bee_pack_validations_total",
                        bee_pack_counters,
                    )
                )
                lines.extend((
                    "# HELP bee_pack_templates_total Bee template pack templates by compatibility status.",
                    "# TYPE bee_pack_templates_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "bee_pack_templates_total",
                        bee_pack_counters,
                    )
                )
                lines.extend((
                    "# HELP bee_pack_dry_runs_total Bee template pack dry-run plans.",
                    "# TYPE bee_pack_dry_runs_total counter",
                ))
                lines.extend(
                    self._format_metric(
                        "bee_pack_dry_runs_total",
                        bee_pack_counters,
                    )
                )
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    @property
    def _span_counters(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0] == "coding_agent_observation_spans_total"
        }

    @property
    def _event_counters(self) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0] == "coding_agent_observation_events_total"
        }

    @property
    def _observation_span_histograms(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[float]]:
        return {
            key: value
            for key, value in self._histograms.items()
            if key[0] == "coding_agent_observation_span_duration_ms"
        }

    @property
    def _http_request_counters(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0] == "coding_agent_http_requests_total"
        }

    @property
    def _http_request_histograms(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[float]]:
        return {
            key: value
            for key, value in self._histograms.items()
            if key[0] == "coding_agent_http_request_duration_ms"
        }

    @property
    def _domain_counters(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0]
            in {
                "coding_agent_evaluation_case_results_total",
                "coding_agent_hitl_interactions_total",
                "coding_agent_storage_operations_total",
            }
        }

    @property
    def _storage_operation_histograms(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[float]]:
        return {
            key: value
            for key, value in self._histograms.items()
            if key[0] == "coding_agent_storage_operation_duration_ms"
        }

    @property
    def _bee_launch_counters(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0]
            in {
                "bee_launches_total",
                "scheduled_bee_launches_total",
                "proactive_bee_launches_total",
            }
        }

    @property
    def _bee_launch_histograms(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[float]]:
        return {
            key: value
            for key, value in self._histograms.items()
            if key[0] == "bee_launch_duration_seconds"
        }

    @property
    def _executor_counters(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0] == "executor_runs_total"
        }

    @property
    def _executor_histograms(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[float]]:
        return {
            key: value
            for key, value in self._histograms.items()
            if key[0] == "executor_run_duration_seconds"
        }

    @property
    def _executor_capability_gauges(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._gauges.items()
            if key[0] == "executor_capability_status"
        }

    @property
    def _topic_recall_counters(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0] == "topic_recall_runs_total"
        }

    @property
    def _topic_recall_histograms(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[float]]:
        return {
            key: value
            for key, value in self._histograms.items()
            if key[0] == "topic_recall_candidates"
        }

    @property
    def _memory_counters(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0] in {"memory_candidates_total", "memory_reviews_total"}
        }

    @property
    def _bee_pack_counters(
        self,
    ) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            key: value
            for key, value in self._counters.items()
            if key[0]
            in {
                "bee_pack_validations_total",
                "bee_pack_templates_total",
                "bee_pack_dry_runs_total",
            }
        }

    def _inc(self, metric: str, labels: Mapping[str, str], amount: float = 1.0) -> None:
        key = (metric, tuple(sorted(labels.items())))
        self._counters[key] = self._counters.get(key, 0.0) + amount

    def _set(self, metric: str, labels: Mapping[str, str], value: float) -> None:
        key = (metric, tuple(sorted(labels.items())))
        self._gauges[key] = value

    def _observe(self, metric: str, labels: Mapping[str, str], value: float) -> None:
        key = (metric, tuple(sorted(labels.items())))
        self._histograms.setdefault(key, []).append(value)

    def _format_metric(
        self,
        expected_metric: str,
        values: Mapping[tuple[str, tuple[tuple[str, str], ...]], float],
    ) -> list[str]:
        lines: list[str] = []
        for (metric, labels), value in sorted(values.items()):
            if metric != expected_metric:
                continue
            lines.append(f"{metric}{_format_labels(labels)} {_format_number(value)}")
        return lines

    def _format_histograms(
        self,
        *,
        histograms: Mapping[tuple[str, tuple[tuple[str, str], ...]], list[float]]
        | None = None,
        metric_name: str = "coding_agent_observation_span_duration_ms",
        buckets: tuple[float, ...] = _PROMETHEUS_HISTOGRAM_BUCKETS,
    ) -> list[str]:
        lines: list[str] = []
        selected_histograms = self._histograms if histograms is None else histograms
        for (_metric, labels), values in sorted(selected_histograms.items()):
            sorted_values = sorted(values)
            base_labels = dict(labels)
            count_so_far = 0
            for bucket in buckets:
                count_so_far = sum(1 for value in sorted_values if value <= bucket)
                bucket_labels = tuple(
                    sorted({**base_labels, "le": _format_number(bucket)}.items())
                )
                lines.append(
                    f"{metric_name}_bucket"
                    f"{_format_labels(bucket_labels)} {count_so_far}"
                )
            inf_labels = tuple(sorted({**base_labels, "le": "+Inf"}.items()))
            lines.append(
                f"{metric_name}_bucket{_format_labels(inf_labels)} {len(values)}"
            )
            lines.append(f"{metric_name}_count{_format_labels(labels)} {len(values)}")
            lines.append(
                f"{metric_name}_sum"
                f"{_format_labels(labels)} {_format_number(sum(values))}"
            )
        return lines


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    formatted = ",".join(
        f'{key}="{_prometheus_escape(value)}"' for key, value in labels
    )
    return "{" + formatted + "}"


_DEFAULT_PROMETHEUS_RECORDER = PrometheusMetricsRecorder()


def prometheus_metrics_text() -> str:
    return _DEFAULT_PROMETHEUS_RECORDER.exposition_text()


def record_http_request_metric(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_ms: float,
) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_http_request(
            method=method,
            route=route,
            status_code=status_code,
            duration_ms=duration_ms,
        )
    except Exception:
        return


def record_evaluation_case_metric(*, status: str) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_evaluation_case_result(status=status)
    except Exception:
        return


def record_hitl_interaction_metric(*, status: str) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_hitl_interaction(status=status)
    except Exception:
        return


def record_storage_operation_metric(
    *,
    operation: str,
    status: str,
    duration_ms: float,
) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_storage_operation(
            operation=operation,
            status=status,
            duration_ms=duration_ms,
        )
    except Exception:
        return


def record_bee_launch_metric(
    *,
    source: str,
    status: str,
    duration_ms: float,
    proactive_kind: str = "unknown",
) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_bee_launch(
            source=source,
            status=status,
            duration_ms=duration_ms,
            proactive_kind=proactive_kind,
        )
    except Exception:
        return


def record_executor_run_metric(
    *,
    executor_kind: str,
    status: str,
    duration_ms: float,
) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_executor_run(
            executor_kind=executor_kind,
            status=status,
            duration_ms=duration_ms,
        )
    except Exception:
        return


def record_executor_capability_metric(
    *,
    executor_kind: str,
    status: str,
) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_executor_capability(
            executor_kind=executor_kind,
            status=status,
        )
    except Exception:
        return


def record_topic_recall_metric(
    *,
    source: str,
    status: str,
    candidate_count: int,
) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_topic_recall_run(
            source=source,
            status=status,
            candidate_count=candidate_count,
        )
    except Exception:
        return


def record_memory_candidate_metric(*, kind: str, status: str) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_memory_candidate(
            kind=kind,
            status=status,
        )
    except Exception:
        return


def record_memory_review_metric(*, status: str) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_memory_review(status=status)
    except Exception:
        return


def record_bee_pack_validation_metric(*, status: str, source_type: str) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_bee_pack_validation(
            status=status,
            source_type=source_type,
        )
    except Exception:
        return


def record_bee_pack_template_metric(*, status: str, source_type: str) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_bee_pack_template(
            status=status,
            source_type=source_type,
        )
    except Exception:
        return


def record_bee_pack_dry_run_metric(*, status: str) -> None:
    try:
        _DEFAULT_PROMETHEUS_RECORDER.record_bee_pack_dry_run(status=status)
    except Exception:
        return


def reset_prometheus_metrics() -> None:
    _DEFAULT_PROMETHEUS_RECORDER.reset()


@dataclass
class OtlpHttpObservationSink:
    """Synchronous OTLP/HTTP JSON span exporter.

    Export failures are intentionally swallowed so observability cannot break
    agent execution.
    """

    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 2.0
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        self.endpoint = _otlp_endpoint(self.endpoint)
        if self.timeout_seconds <= 0:
            raise ValueError("observability.timeout_seconds must be positive")

    def record_span(self, span: SpanRecord) -> None:
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self.client is None
        try:
            response = client.post(
                self.endpoint,
                json=self._payload(span),
                headers=self.headers,
            )
            response.raise_for_status()
        except Exception:
            return
        finally:
            if close_client:
                client.close()

    def record_event(self, event: ObservationEvent) -> None:
        del event

    def _payload(self, span: SpanRecord) -> dict[str, Any]:
        status_code = 2 if span.status == "error" else 1
        attributes = dict(span.attributes)
        if span.error_type is not None:
            attributes["error.type"] = span.error_type
        if span.error_message is not None:
            attributes["error.message"] = span.error_message
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": _resource_attributes(attributes)},
                    "scopeSpans": [
                        {
                            "scope": {"name": "coding_agent"},
                            "spans": [
                                {
                                    "traceId": _trace_id(attributes),
                                    "spanId": secrets.token_hex(8),
                                    "name": span.name,
                                    "kind": 1,
                                    "startTimeUnixNano": _nanos(span.start_time),
                                    "endTimeUnixNano": _nanos(span.end_time),
                                    "attributes": _otlp_attributes(attributes),
                                    "status": {"code": status_code},
                                }
                            ],
                        }
                    ],
                }
            ]
        }


def _string_map(value: Any, *, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a table")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only string values")
        result[key] = item
    return result


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise ValueError(f"{name} must be set for Langfuse observability")
    return value


def _langfuse_headers(config: Mapping[str, Any]) -> dict[str, str]:
    public_key_env = config.get("public_key_env", "LANGFUSE_PUBLIC_KEY")
    secret_key_env = config.get("secret_key_env", "LANGFUSE_SECRET_KEY")
    if not isinstance(public_key_env, str) or not public_key_env:
        raise ValueError("observability.public_key_env must be a non-empty string")
    if not isinstance(secret_key_env, str) or not secret_key_env:
        raise ValueError("observability.secret_key_env must be a non-empty string")
    public_key = _required_env(public_key_env)
    secret_key = _required_env(secret_key_env)
    encoded = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode(
        "ascii"
    )
    headers = _string_map(config.get("headers"), field_name="observability.headers")
    headers["authorization"] = f"Basic {encoded}"
    return headers


def _enabled(config: Mapping[str, Any]) -> bool:
    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("observability.enabled must be a boolean")
    return enabled


def _build_tracing_sink(config: Mapping[str, Any]) -> ObservationSink | None:
    if not _enabled(config):
        return None
    backend = config.get("backend", "noop")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError("observability.backend must be a non-empty string")
    if backend == "noop":
        return NoopObservationSink()
    if backend not in {"otlp_http", "langfuse"}:
        raise ValueError(f"unsupported observability backend: {backend}")

    endpoint = config.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("observability.endpoint must be a non-empty string")
    timeout = config.get("timeout_seconds", 2.0)
    if not isinstance(timeout, int | float) or isinstance(timeout, bool):
        raise ValueError("observability.timeout_seconds must be a number")
    headers = (
        _langfuse_headers(config)
        if backend == "langfuse"
        else _string_map(config.get("headers"), field_name="observability.headers")
    )
    return OtlpHttpObservationSink(
        endpoint=endpoint,
        headers=headers,
        timeout_seconds=float(timeout),
    )


def _build_metrics_sink(config: Mapping[str, Any]) -> ObservationSink | None:
    if not _enabled(config):
        return None
    backend = config.get("backend", "prometheus")
    if not isinstance(backend, str) or not backend.strip():
        raise ValueError("observability.metrics.backend must be a non-empty string")
    if backend != "prometheus":
        raise ValueError(f"unsupported observability metrics backend: {backend}")
    return PrometheusMetricsObservationSink()


def _table(value: Any, *, field_name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a table")
    return value


def _has_flat_tracing_config(config: Mapping[str, Any]) -> bool:
    return any(key in config for key in ("backend", "endpoint", "headers"))


def build_observation_sink(config: Mapping[str, Any]) -> ObservationSink | None:
    """Build the configured observation sink.

    The default is intentionally disabled. This module owns product-level
    exporter configuration; agentkit remains provider-neutral.
    """

    if not _enabled(config):
        return None

    tracing_config = _table(config.get("tracing"), field_name="observability.tracing")
    metrics_config = _table(config.get("metrics"), field_name="observability.metrics")
    if tracing_config is None and metrics_config is None:
        return _build_tracing_sink(config)

    sinks: list[ObservationSink] = []
    if tracing_config is not None:
        tracing_sink = _build_tracing_sink(tracing_config)
        if tracing_sink is not None:
            sinks.append(tracing_sink)
    elif _has_flat_tracing_config(config):
        tracing_sink = _build_tracing_sink(config)
        if tracing_sink is not None:
            sinks.append(tracing_sink)
    if metrics_config is not None:
        metrics_sink = _build_metrics_sink(metrics_config)
        if metrics_sink is not None:
            sinks.append(metrics_sink)

    if not sinks:
        return NoopObservationSink()
    if len(sinks) == 1:
        return sinks[0]
    return CompositeObservationSink(tuple(sinks))
