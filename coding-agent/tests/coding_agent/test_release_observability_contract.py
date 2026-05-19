from __future__ import annotations

import json

import httpx

from agentkit.environment import WorkspaceSummary
from agentkit.observability import SpanRecord
from agentkit.runtime.context import AgentRunContext
from agentkit.runtime.pipeline import (
    _TRACE_METADATA_ATTRIBUTE_KEYS,
    _safe_trace_metadata_attributes,
)
from coding_agent.action_safety import (
    ActionKind,
    ActionObservation,
    ActionObservationStatus,
)
from coding_agent.context_pack import ContextPack, ContextPackItem, ContextPackSection
from coding_agent.kb import DocumentChunk, KBSearchResult
from coding_agent.observability import OtlpHttpObservationSink
from coding_agent.plugins.kb import (
    _context_pack_attributes,
    _retrieval_result_attributes,
)


_FORBIDDEN_ATTRIBUTE_PARTS = (
    "content",
    "message",
    "prompt",
    "result",
    "secret",
    "text",
)
_SENSITIVE_SENTINEL = "SECRET_PROMPT_RESULT_TEXT"


class DummyEnvironment:
    @property
    def kind(self) -> str:
        return "dummy"

    def tool_config(self) -> dict[str, object]:
        return {}

    def workspace_summary(self) -> WorkspaceSummary:
        return WorkspaceSummary(display_name="dummy")

    def build_file_tools(self):
        raise NotImplementedError

    def build_file_patch_tool(self):
        raise NotImplementedError

    def build_shell_tool(self):
        raise NotImplementedError


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200)


def test_otlp_exporter_drops_sensitive_attribute_keys_and_error_messages() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="release.safety",
            status="error",
            attributes={
                "session_id": "session-1",
                "run_id": "run-1",
                "safe_count": 3,
                "prompt": _SENSITIVE_SENTINEL,
                "tool.result": _SENSITIVE_SENTINEL,
                "secret.value": _SENSITIVE_SENTINEL,
            },
            error_type="RuntimeError",
            error_message=f"raw failure output {_SENSITIVE_SENTINEL}",
        )
    )

    assert len(transport.requests) == 1
    payload = json.loads(transport.requests[0].read().decode())
    serialized = json.dumps(payload)

    assert "safe_count" in serialized
    assert "RuntimeError" in serialized
    assert _SENSITIVE_SENTINEL not in serialized
    _assert_safe_attribute_keys(_span_attribute_keys(payload))


def test_runtime_trace_metadata_exports_only_safe_correlation_keys() -> None:
    run_context = AgentRunContext(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        environment=DummyEnvironment(),
        trace_metadata={
            "turn_id": "turn-1",
            "tape_id": "tape-1",
            "tool_call_id": "tool-1",
            "interaction_id": "interaction-1",
            "event_id": "event-1",
            "checkpoint_id": "checkpoint-1",
            "prompt": _SENSITIVE_SENTINEL,
            "message": _SENSITIVE_SENTINEL,
            "result": _SENSITIVE_SENTINEL,
            "secret": _SENSITIVE_SENTINEL,
            "nested": {"text": _SENSITIVE_SENTINEL},
        },
    )

    attributes = _safe_trace_metadata_attributes(run_context)

    assert set(attributes) == _TRACE_METADATA_ATTRIBUTE_KEYS
    _assert_safe_attribute_keys(attributes)
    assert _SENSITIVE_SENTINEL not in json.dumps(attributes)


def test_release_observation_attribute_factories_use_safe_metadata_keys() -> None:
    action_attributes = ActionObservation(
        kind=ActionKind.VALIDATION,
        status=ActionObservationStatus.COMPLETED,
        policy_decision="allow",
        policy_reasons=("validation_command",),
        risk_level="low",
        changed_path_count=1,
        file_extension_buckets=("py",),
        command_label="unit-tests",
        exit_code=0,
        duration_ms=12,
        dry_run=False,
    ).to_attributes()
    retrieval_attributes = _retrieval_result_attributes(
        [
            KBSearchResult(
                chunk=DocumentChunk(
                    id="chunk-1",
                    content=_SENSITIVE_SENTINEL,
                    source="src/app.py",
                    metadata={"source_kind": "repo_file"},
                ),
                score=0.9,
            )
        ],
        cache_hit=False,
        top_k=5,
    )
    pack_attributes = _context_pack_attributes(
        ContextPack(
            sections=(
                ContextPackSection(
                    title="Repo references",
                    items=(
                        ContextPackItem(
                            source_kind="repo_file",
                            source_id="source-1",
                            label="src/app.py",
                            body=_SENSITIVE_SENTINEL,
                            score=0.9,
                        ),
                    ),
                ),
            )
        )
    )

    for attributes in (action_attributes, retrieval_attributes, pack_attributes):
        _assert_safe_attribute_keys(attributes)
        assert _SENSITIVE_SENTINEL not in json.dumps(attributes)


def _span_attribute_keys(payload: dict[str, object]) -> set[str]:
    resource_spans = payload["resourceSpans"]
    assert isinstance(resource_spans, list)
    scope_spans = resource_spans[0]["scopeSpans"]
    assert isinstance(scope_spans, list)
    spans = scope_spans[0]["spans"]
    assert isinstance(spans, list)
    attributes = spans[0]["attributes"]
    assert isinstance(attributes, list)
    return {
        item["key"]
        for item in attributes
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }


def _assert_safe_attribute_keys(attributes: dict[str, object] | set[str]) -> None:
    keys = attributes if isinstance(attributes, set) else set(attributes)
    offenders = sorted(
        key
        for key in keys
        for forbidden in _FORBIDDEN_ATTRIBUTE_PARTS
        if forbidden in key.casefold()
    )
    assert offenders == []
