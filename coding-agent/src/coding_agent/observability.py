"""Coding Agent observability configuration and sink construction."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from agentkit.observability import (
    NoopObservationSink,
    ObservationEvent,
    ObservationSink,
    SpanRecord,
)

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


def _otlp_endpoint(endpoint: str) -> str:
    stripped = endpoint.rstrip("/")
    if stripped.endswith("/v1/traces"):
        return stripped
    return f"{stripped}/v1/traces"


def _attribute_allowed(key: str) -> bool:
    key_folded = key.casefold()
    return not any(part in key_folded for part in _SENSITIVE_ATTRIBUTE_PARTS)


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
        if _attribute_allowed(key)
    ]


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


def build_observation_sink(config: Mapping[str, Any]) -> ObservationSink | None:
    """Build the configured observation sink.

    The default is intentionally disabled. This module owns product-level
    exporter configuration; agentkit remains provider-neutral.
    """

    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("observability.enabled must be a boolean")
    if not enabled:
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
