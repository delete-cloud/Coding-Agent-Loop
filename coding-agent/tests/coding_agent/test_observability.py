from __future__ import annotations

import base64
import json

import httpx
import pytest

from agentkit.observability import NoopObservationSink, ObservationEvent, SpanRecord
from coding_agent.observability import (
    CompositeObservationSink,
    OtlpHttpObservationSink,
    PrometheusMetricsObservationSink,
    PrometheusMetricsRecorder,
    build_observation_sink,
    prometheus_metrics_text,
    record_evaluation_case_metric,
    record_hitl_interaction_metric,
    record_http_request_metric,
    record_storage_operation_metric,
    reset_prometheus_metrics,
)


class RecordingTransport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code)


class RecordingObservationSink:
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


class FailingPrometheusMetricsRecorder(PrometheusMetricsRecorder):
    def record_span(self, span: SpanRecord) -> None:
        del span
        raise RuntimeError("registry failed")

    def record_event(self, event: ObservationEvent) -> None:
        del event
        raise RuntimeError("registry failed")


def _payload(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.read().decode())


def _first_span(payload: dict[str, object]) -> dict[str, object]:
    resource_spans = payload["resourceSpans"]
    assert isinstance(resource_spans, list)
    scope_spans = resource_spans[0]["scopeSpans"]
    assert isinstance(scope_spans, list)
    spans = scope_spans[0]["spans"]
    assert isinstance(spans, list)
    span = spans[0]
    assert isinstance(span, dict)
    return span


def _resource_attributes(payload: dict[str, object]) -> dict[str, object]:
    resource_spans = payload["resourceSpans"]
    assert isinstance(resource_spans, list)
    resource = resource_spans[0]["resource"]
    assert isinstance(resource, dict)
    attributes = resource["attributes"]
    assert isinstance(attributes, list)
    result: dict[str, object] = {}
    for item in attributes:
        assert isinstance(item, dict)
        key = item["key"]
        assert isinstance(key, str)
        value = item["value"]
        assert isinstance(value, dict)
        result[key] = value
    return result


def test_composite_observation_sink_records_spans_and_events_to_all_sinks() -> None:
    first = RecordingObservationSink()
    second = RecordingObservationSink()
    sink = CompositeObservationSink((first, second))
    span = SpanRecord(name="runtime.stage.build_context", status="ok")
    event = ObservationEvent(name="runtime.started")

    sink.record_span(span)
    sink.record_event(event)

    assert first.spans == [span]
    assert second.spans == [span]
    assert first.events == [event]
    assert second.events == [event]


def test_composite_observation_sink_fail_opens_when_child_sink_fails() -> None:
    recording = RecordingObservationSink()
    sink = CompositeObservationSink((FailingObservationSink(), recording))
    span = SpanRecord(name="tool.call", status="ok")
    event = ObservationEvent(name="tool.call.completed")

    sink.record_span(span)
    sink.record_event(event)

    assert recording.spans == [span]
    assert recording.events == [event]


def test_prometheus_metrics_record_spans_events_counters_histograms_and_gauges() -> (
    None
):
    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)

    sink.record_span(
        SpanRecord(
            name="runtime.stage.build_context",
            status="ok",
            attributes={"stage": "build_context", "provider": "anthropic"},
            duration_ms=42.5,
        )
    )
    sink.record_event(
        ObservationEvent(
            name="runtime.started",
            attributes={"stage": "build_context"},
            timestamp=123.0,
        )
    )

    text = recorder.exposition_text()

    assert (
        'coding_agent_observation_spans_total{provider="anthropic",span="runtime.stage.build_context",stage="build_context",status="ok"} 1'
        in text
    )
    assert (
        'coding_agent_observation_span_duration_ms_count{provider="anthropic",span="runtime.stage.build_context",stage="build_context",status="ok"} 1'
        in text
    )
    assert (
        'coding_agent_observation_span_duration_ms_sum{provider="anthropic",span="runtime.stage.build_context",stage="build_context",status="ok"} 42.5'
        in text
    )
    assert (
        'coding_agent_observation_events_total{event="runtime.started",stage="build_context"} 1'
        in text
    )
    assert (
        'coding_agent_observation_last_event_timestamp_seconds{event="runtime.started",stage="build_context"} 123'
        in text
    )


def test_prometheus_metrics_drop_forbidden_high_cardinality_labels() -> None:
    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)

    sink.record_span(
        SpanRecord(
            name="tool.call",
            status="ok",
            attributes={
                "stage": "dispatch",
                "run_id": "run-1",
                "session_id": "session-1",
                "trace_id": "trace-1",
                "event_id": "event-1",
                "interaction_id": "interaction-1",
                "tool_call_id": "tool-call-1",
                "file_path": "src/secret.py",
                "prompt": "raw prompt",
                "message": "raw message",
                "content": "raw content",
                "command_output": "raw output",
                "secret": "raw secret",
            },
            duration_ms=1,
        )
    )

    text = recorder.exposition_text()

    assert 'stage="dispatch"' in text
    for forbidden in (
        "run_id",
        "session_id",
        "trace_id",
        "event_id",
        "interaction_id",
        "tool_call_id",
        "file_path",
        "prompt",
        "message",
        "content",
        "command_output",
        "secret",
        "raw prompt",
        "raw message",
        "raw content",
        "raw output",
        "raw secret",
        "src/secret.py",
    ):
        assert forbidden not in text


def test_prometheus_metrics_allow_low_cardinality_topic_labels_without_topic_id() -> (
    None
):
    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)

    sink.record_span(
        SpanRecord(
            name="context_pack.build",
            status="ok",
            attributes={
                "topic_id": "topic-auth",
                "topic_kind": "coding",
                "topic_status": "finalized",
                "topic_profile": "local",
            },
            duration_ms=1,
        )
    )

    text = recorder.exposition_text()

    assert 'topic_kind="coding"' in text
    assert 'topic_status="finalized"' in text
    assert 'topic_profile="local"' in text
    assert "topic_id" not in text
    assert "topic-auth" not in text


def test_prometheus_metrics_normalize_unlisted_topic_kind_to_unknown() -> None:
    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)

    sink.record_span(
        SpanRecord(
            name="context_pack.build",
            status="ok",
            attributes={
                "topic_kind": "customer_auth_cleanup_123",
                "topic_status": "finalized",
            },
            duration_ms=1,
        )
    )

    text = recorder.exposition_text()

    assert 'topic_kind="unknown"' in text
    assert "customer_auth_cleanup_123" not in text


def test_prometheus_metrics_normalize_unsafe_allowed_values_and_reserved_labels() -> (
    None
):
    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)

    sink.record_span(
        SpanRecord(
            name="custom.span.with.raw.name",
            status="ok",
            attributes={
                "status": "raw prompt secret status",
                "stage": "raw prompt secret stage",
                "provider": "raw prompt secret provider",
            },
            error_type="RuntimeError",
            duration_ms=1,
        )
    )
    sink.record_event(
        ObservationEvent(
            name="custom.event.with.raw.name",
            attributes={"event": "raw prompt secret event"},
            timestamp=1,
        )
    )

    text = recorder.exposition_text()

    assert 'span="unknown"' in text
    assert 'event="unknown"' in text
    assert 'status="ok"' in text
    assert 'stage="unknown"' in text
    assert "raw_prompt_secret" not in text
    assert "custom.span" not in text
    assert "custom.event" not in text


def test_prometheus_metrics_event_only_exposition_omits_span_metadata() -> None:
    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)

    sink.record_event(ObservationEvent(name="runtime.started", timestamp=123.0))

    text = recorder.exposition_text()

    assert "coding_agent_observation_events_total" in text
    assert "# TYPE coding_agent_observation_spans_total counter" not in text
    assert "coding_agent_observation_spans_total" not in text


def test_prometheus_metrics_record_http_request_metrics() -> None:
    reset_prometheus_metrics()

    record_http_request_metric(
        method="GET",
        route="/healthz",
        status_code=200,
        duration_ms=12.5,
    )

    text = prometheus_metrics_text()

    assert (
        'coding_agent_http_requests_total{method="GET",route="healthz",status_code="200"} 1'
        in text
    )
    assert (
        'coding_agent_http_request_duration_ms_count{method="GET",route="healthz",status_code="200"} 1'
        in text
    )
    assert (
        'coding_agent_http_request_duration_ms_sum{method="GET",route="healthz",status_code="200"} 12.5'
        in text
    )


def test_prometheus_metrics_map_representative_runtime_context_action_spans() -> None:
    recorder = PrometheusMetricsRecorder()
    sink = PrometheusMetricsObservationSink(recorder=recorder)

    sink.record_span(
        SpanRecord(
            name="runtime.stage.build_context",
            status="ok",
            attributes={"stage": "build_context"},
            duration_ms=4,
        )
    )
    sink.record_span(
        SpanRecord(
            name="retrieval.kb.search",
            status="ok",
            attributes={
                "retrieval.cache_hit": False,
                "retrieval.source_kind": "kb",
            },
            duration_ms=5,
        )
    )
    sink.record_span(
        SpanRecord(
            name="context_pack.render",
            status="ok",
            attributes={},
            duration_ms=6,
        )
    )
    sink.record_span(
        SpanRecord(
            name="action_safety.action",
            status="ok",
            attributes={
                "action_kind": "file_edit",
                "action_status": "completed",
                "policy_decision": "allow",
                "risk_level": "low",
            },
            duration_ms=7,
        )
    )
    sink.record_span(
        SpanRecord(
            name="action_safety.action",
            status="ok",
            attributes={"action_kind": "command", "action_status": "started"},
            duration_ms=1,
        )
    )
    sink.record_event(
        ObservationEvent(
            name="action_safety.action",
            attributes={"action_kind": "validation", "action_status": "completed"},
        )
    )

    text = recorder.exposition_text()

    assert 'span="runtime.stage.build_context"' in text
    assert 'stage="build_context"' in text
    assert 'span="retrieval.kb.search"' in text
    assert 'cache_hit="false"' in text
    assert 'source_kind="kb"' in text
    assert 'span="context_pack.render"' in text
    assert 'span="action_safety.action"' in text
    assert 'event="action_safety.action"' in text
    assert 'action_kind="file_edit"' in text
    assert 'action_kind="command"' in text
    assert 'action_kind="validation"' in text
    assert 'action_status="completed"' in text
    assert 'policy_decision="allow"' in text
    assert 'risk_level="low"' in text


def test_prometheus_metrics_record_eval_hitl_and_storage_outcomes() -> None:
    recorder = PrometheusMetricsRecorder()

    recorder.record_evaluation_case_result(status="passed")
    recorder.record_hitl_interaction(status="approved")
    recorder.record_storage_operation(
        operation="checkpoint_save",
        status="ok",
        duration_ms=11,
    )

    text = recorder.exposition_text()

    assert 'coding_agent_evaluation_case_results_total{eval_status="passed"} 1' in text
    assert 'coding_agent_hitl_interactions_total{hitl_status="approved"} 1' in text
    assert (
        'coding_agent_storage_operations_total{operation="checkpoint_save",storage_status="ok"} 1'
        in text
    )
    assert (
        'coding_agent_storage_operation_duration_ms_count{operation="checkpoint_save",storage_status="ok"} 1'
        in text
    )


def test_prometheus_metrics_record_eval_hitl_and_storage_on_default_recorder() -> None:
    reset_prometheus_metrics()

    record_evaluation_case_metric(status="passed")
    record_hitl_interaction_metric(status="approved")
    record_storage_operation_metric(
        operation="checkpoint_save",
        status="ok",
        duration_ms=11,
    )

    text = prometheus_metrics_text()

    assert 'coding_agent_evaluation_case_results_total{eval_status="passed"} 1' in text
    assert 'coding_agent_hitl_interactions_total{hitl_status="approved"} 1' in text
    assert (
        'coding_agent_storage_operations_total{operation="checkpoint_save",storage_status="ok"} 1'
        in text
    )


def test_prometheus_metrics_fail_open_when_registry_write_fails() -> None:
    sink = PrometheusMetricsObservationSink(recorder=FailingPrometheusMetricsRecorder())

    sink.record_span(SpanRecord(name="tool.call", status="ok"))
    sink.record_event(ObservationEvent(name="tool.call.completed"))


def test_otlp_sink_posts_span_without_prompt_or_output_content() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="llm.generation",
            status="ok",
            attributes={
                "session_id": "session-1",
                "input_tokens": 10,
                "content": "content-do-not-send",
                "message": "message-do-not-send",
                "prompt": "do-not-send",
                "result": "result-do-not-send",
                "secret": "secret-do-not-send",
                "text": "text-do-not-send",
            },
            start_time=1.0,
            end_time=2.0,
            duration_ms=1000,
        )
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == "https://otel.example.test/v1/traces"
    body = request.read().decode()
    assert "llm.generation" in body
    assert "input_tokens" in body
    assert "do-not-send" not in body
    assert "content-do-not-send" not in body
    assert "message-do-not-send" not in body
    assert "result-do-not-send" not in body
    assert "secret-do-not-send" not in body
    assert "text-do-not-send" not in body
    span = _first_span(json.loads(body))
    exported_keys = {
        item["key"]
        for item in span.get("attributes", [])
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    assert {"content", "message", "prompt", "result", "secret", "text"}.isdisjoint(
        exported_keys
    )


def test_otlp_sink_groups_spans_by_session_and_run() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="runtime.stage.build_context",
            status="ok",
            attributes={"session_id": "session-1", "run_id": "run-1"},
        )
    )
    sink.record_span(
        SpanRecord(
            name="llm.generation",
            status="ok",
            attributes={"session_id": "session-1", "run_id": "run-1"},
        )
    )
    sink.record_span(
        SpanRecord(
            name="tool.call",
            status="ok",
            attributes={"session_id": "session-1", "run_id": "run-2"},
        )
    )

    first_trace_id = _first_span(_payload(transport.requests[0]))["traceId"]
    second_trace_id = _first_span(_payload(transport.requests[1]))["traceId"]
    third_trace_id = _first_span(_payload(transport.requests[2]))["traceId"]
    assert first_trace_id == second_trace_id
    assert third_trace_id != first_trace_id


def test_otlp_sink_exports_session_and_run_as_resource_attributes() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="llm.generation",
            status="ok",
            attributes={"session_id": "session-1", "run_id": "run-1"},
        )
    )

    resource_attributes = _resource_attributes(_payload(transport.requests[0]))
    assert resource_attributes["session.id"] == {"stringValue": "session-1"}
    assert resource_attributes["run.id"] == {"stringValue": "run-1"}


def test_otlp_sink_fail_opens_when_export_fails() -> None:
    transport = RecordingTransport(status_code=500)
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(SpanRecord(name="tool.call", status="ok"))

    assert len(transport.requests) == 1


def test_build_observation_sink_builds_otlp_http_sink() -> None:
    sink = build_observation_sink(
        {
            "enabled": True,
            "backend": "otlp_http",
            "endpoint": "https://otel.example.test/api/public/otel",
            "headers": {"x-test": "yes"},
        }
    )

    assert isinstance(sink, OtlpHttpObservationSink)
    assert sink.endpoint == "https://otel.example.test/api/public/otel/v1/traces"


def test_build_observation_sink_supports_tracing_only() -> None:
    sink = build_observation_sink(
        {
            "enabled": True,
            "tracing": {
                "enabled": True,
                "backend": "otlp_http",
                "endpoint": "https://otel.example.test/api/public/otel",
            },
        }
    )

    assert isinstance(sink, OtlpHttpObservationSink)
    assert sink.endpoint == "https://otel.example.test/api/public/otel/v1/traces"


def test_build_observation_sink_supports_metrics_only() -> None:
    sink = build_observation_sink(
        {
            "enabled": True,
            "metrics": {
                "enabled": True,
                "backend": "prometheus",
            },
        }
    )

    assert isinstance(sink, PrometheusMetricsObservationSink)


def test_build_observation_sink_supports_tracing_and_metrics() -> None:
    sink = build_observation_sink(
        {
            "enabled": True,
            "tracing": {
                "enabled": True,
                "backend": "otlp_http",
                "endpoint": "https://otel.example.test/api/public/otel",
            },
            "metrics": {
                "enabled": True,
                "backend": "prometheus",
            },
        }
    )

    assert isinstance(sink, CompositeObservationSink)
    assert len(sink.sinks) == 2
    assert isinstance(sink.sinks[0], OtlpHttpObservationSink)
    assert isinstance(sink.sinks[1], PrometheusMetricsObservationSink)


def test_build_observation_sink_preserves_flat_tracing_when_metrics_are_nested() -> (
    None
):
    sink = build_observation_sink(
        {
            "enabled": True,
            "backend": "otlp_http",
            "endpoint": "https://otel.example.test/api/public/otel",
            "metrics": {
                "enabled": True,
                "backend": "prometheus",
            },
        }
    )

    assert isinstance(sink, CompositeObservationSink)
    assert len(sink.sinks) == 2
    assert isinstance(sink.sinks[0], OtlpHttpObservationSink)
    assert (
        sink.sinks[0].endpoint == "https://otel.example.test/api/public/otel/v1/traces"
    )
    assert isinstance(sink.sinks[1], PrometheusMetricsObservationSink)


def test_build_observation_sink_returns_none_when_disabled() -> None:
    assert build_observation_sink({"enabled": False}) is None


def test_build_observation_sink_returns_noop_when_all_nested_backends_disabled() -> (
    None
):
    sink = build_observation_sink(
        {
            "enabled": True,
            "tracing": {"enabled": False},
            "metrics": {"enabled": False},
        }
    )

    assert isinstance(sink, NoopObservationSink)


def test_build_observation_sink_builds_langfuse_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    sink = build_observation_sink(
        {
            "enabled": True,
            "backend": "langfuse",
            "endpoint": "https://cloud.langfuse.com/api/public/otel",
        }
    )

    assert isinstance(sink, OtlpHttpObservationSink)
    encoded = base64.b64encode(b"pk-test:sk-test").decode("ascii")
    assert sink.headers["authorization"] == f"Basic {encoded}"


def test_build_observation_sink_rejects_missing_langfuse_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    with pytest.raises(ValueError, match="LANGFUSE_SECRET_KEY"):
        build_observation_sink(
            {
                "enabled": True,
                "backend": "langfuse",
                "endpoint": "https://cloud.langfuse.com/api/public/otel",
            }
        )
