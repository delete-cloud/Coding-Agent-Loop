from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agentkit.observability import ObservationEvent, SpanRecord
from coding_agent.observability import (
    CompositeObservationSink,
    OtlpHttpObservationSink,
    PrometheusMetricsObservationSink,
    prometheus_metrics_text,
    record_evaluation_case_metric,
    record_hitl_interaction_metric,
    record_storage_operation_metric,
    reset_prometheus_metrics,
)
import coding_agent.ui.http_server as http_server


SENSITIVE_SENTINEL = "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT"
FORBIDDEN_PROMETHEUS_PARTS = (
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
    SENSITIVE_SENTINEL,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200)


@pytest.mark.asyncio
async def test_observability_platform_metrics_endpoint_smoke(monkeypatch) -> None:
    reset_prometheus_metrics()
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "metrics": {"enabled": True, "endpoint_enabled": True},
        },
    )
    transport = ASGITransport(app=http_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        metrics = await client.get("/metrics")

    assert health.status_code == 200
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")
    assert "coding_agent_http_requests_total" in metrics.text
    assert "coding_agent_http_request_duration_ms_count" in metrics.text
    assert 'method="GET"' in metrics.text
    assert 'route="healthz"' in metrics.text
    assert 'status_code="200"' in metrics.text
    _assert_no_prometheus_leak(metrics.text)
    reset_prometheus_metrics()


def test_observability_platform_composite_tracing_and_metrics_smoke() -> None:
    reset_prometheus_metrics()
    transport = RecordingTransport()
    sink = CompositeObservationSink(
        (
            OtlpHttpObservationSink(
                endpoint="https://otel.example.test/v1/traces",
                client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
            ),
            PrometheusMetricsObservationSink(),
        )
    )

    sink.record_span(
        SpanRecord(
            name="runtime.stage.build_context",
            status="ok",
            attributes={
                "stage": "build_context",
                "run_id": "run-1",
                "session_id": "session-1",
                "prompt": SENSITIVE_SENTINEL,
                "message": SENSITIVE_SENTINEL,
                "content": SENSITIVE_SENTINEL,
                "command_output": SENSITIVE_SENTINEL,
                "secret": SENSITIVE_SENTINEL,
            },
            duration_ms=12,
        )
    )
    sink.record_span(
        SpanRecord(
            name="retrieval.kb.search",
            status="ok",
            attributes={
                "retrieval.source_kind": "repo_file",
                "retrieval.cache_hit": True,
            },
            duration_ms=7,
        )
    )
    sink.record_span(
        SpanRecord(
            name="action_safety.action",
            status="ok",
            attributes={
                "action_kind": "command",
                "action_status": "completed",
                "policy_decision": "allow",
                "risk_level": "low",
            },
            duration_ms=5,
        )
    )
    sink.record_event(
        ObservationEvent(
            name="action_safety.action",
            attributes={"action_kind": "command", "action_status": "completed"},
            timestamp=123.0,
        )
    )
    record_evaluation_case_metric(status="passed")
    record_hitl_interaction_metric(status="approved")
    record_storage_operation_metric(
        operation="checkpoint_save",
        status="ok",
        duration_ms=9,
    )

    metrics = prometheus_metrics_text()
    assert 'span="runtime.stage.build_context"' in metrics
    assert 'span="retrieval.kb.search"' in metrics
    assert 'span="action_safety.action"' in metrics
    assert 'event="action_safety.action"' in metrics
    assert 'source_kind="repo_file"' in metrics
    assert 'cache_hit="true"' in metrics
    assert 'action_kind="command"' in metrics
    assert 'action_status="completed"' in metrics
    assert 'eval_status="passed"' in metrics
    assert 'hitl_status="approved"' in metrics
    assert 'operation="checkpoint_save"' in metrics
    assert 'storage_status="ok"' in metrics
    assert "coding_agent_observation_span_duration_ms_count" in metrics
    assert "coding_agent_evaluation_case_results_total" in metrics
    assert "coding_agent_hitl_interactions_total" in metrics
    assert "coding_agent_storage_operations_total" in metrics
    _assert_no_prometheus_leak(metrics)

    assert len(transport.requests) == 3
    serialized_trace_payloads = json.dumps(
        [json.loads(request.read().decode()) for request in transport.requests]
    )
    assert SENSITIVE_SENTINEL not in serialized_trace_payloads
    for forbidden in ("prompt", "message", "content", "command_output", "secret"):
        assert forbidden not in serialized_trace_payloads
    reset_prometheus_metrics()


def _assert_no_prometheus_leak(text: str) -> None:
    for forbidden in FORBIDDEN_PROMETHEUS_PARTS:
        assert forbidden not in text
