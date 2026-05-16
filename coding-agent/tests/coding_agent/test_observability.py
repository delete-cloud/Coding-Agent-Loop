from __future__ import annotations

import base64

import httpx
import pytest

from agentkit.observability import SpanRecord
from coding_agent.observability import (
    OtlpHttpObservationSink,
    build_observation_sink,
)


class RecordingTransport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code)


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
                "prompt": "do-not-send",
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
