from __future__ import annotations

import json

import httpx

from agentkit.observability import SpanRecord
from agentkit.tape.extract import ToolCallRecord, TurnTrace
from coding_agent.agent_observability import (
    AgentObservationRecorder,
    JsonlAgentObservationStore,
    sanitized_turn_projection,
)
from coding_agent.observability import OtlpHttpObservationSink


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200)


def _jsonl_records(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_recorder_persists_turn_started_before_turn_finishes(tmp_path) -> None:
    store = JsonlAgentObservationStore(tmp_path)
    recorder = AgentObservationRecorder(store=store)

    recorder.start_turn(session_id="session-1", run_id="run-1", prompt="raw prompt")

    records = _jsonl_records(tmp_path / "runs" / "run-1" / "observations.jsonl")
    assert [record["kind"] for record in records] == ["turn.started"]
    assert records[0]["attributes"] == {"user_length": 10}
    assert "raw prompt" not in json.dumps(records)


def test_tool_events_persist_shape_without_raw_arguments_or_result(tmp_path) -> None:
    store = JsonlAgentObservationStore(tmp_path)
    recorder = AgentObservationRecorder(store=store)
    recorder.start_turn(session_id="session-1", run_id="run-1", prompt="hello")

    recorder.observe_tool_call(
        tool_name="bash_run",
        tool_call_id="tool-1",
        arguments={
            "cmd": "cat /tmp/secret.txt",
            "api_key": "sk-raw-secret",
            "count": 3,
        },
    )
    recorder.observe_tool_result(
        tool_name="bash_run",
        tool_call_id="tool-1",
        result="stdout says sk-raw-secret",
        is_error=False,
    )

    body = (tmp_path / "runs" / "run-1" / "observations.jsonl").read_text(
        encoding="utf-8"
    )
    assert "cat /tmp/secret.txt" not in body
    assert "sk-raw-secret" not in body
    assert "stdout says" not in body

    records = _jsonl_records(tmp_path / "runs" / "run-1" / "observations.jsonl")
    tool_call = records[1]
    assert tool_call["kind"] == "tool.call.requested"
    assert tool_call["attributes"]["tool_name"] == "bash_run"
    assert tool_call["attributes"]["arg_shape"] == {
        "field_count": 3,
        "fields": [
            {"name": "cmd", "type": "str", "string_length": 19},
            {"name": "redacted_key", "type": "str", "string_length": 13},
            {"name": "count", "type": "int"},
        ],
    }
    tool_result = records[2]
    assert tool_result["kind"] == "tool.result.observed"
    assert tool_result["attributes"]["result_shape"] == {
        "type": "str",
        "length": 25,
        "line_count": 1,
    }


def test_sanitized_turn_projection_excludes_raw_turn_content() -> None:
    projection = sanitized_turn_projection(
        TurnTrace(
            user_input="please run cat /tmp/secret.txt",
            tool_calls=(
                ToolCallRecord(
                    call_id="tool-1",
                    name="bash_run",
                    arguments={"cmd": "cat /tmp/secret.txt"},
                    result_content="stdout says sk-raw-secret",
                ),
            ),
            final_output="the secret was sk-raw-secret",
        )
    )

    serialized = json.dumps(projection, sort_keys=True)
    assert "cat /tmp/secret.txt" not in serialized
    assert "sk-raw-secret" not in serialized
    assert "stdout says" not in serialized
    assert projection["input"] == {"user_present": True, "user_length": 30}
    assert projection["output"]["final_present"] is True
    assert projection["output"]["final_length"] == 28
    assert projection["output"]["tool_call_count"] == 1


def test_otlp_sink_exports_sanitized_langfuse_observation_payload() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )
    projection = sanitized_turn_projection(
        TurnTrace(
            user_input="raw prompt with sk-raw-secret",
            tool_calls=(),
            final_output="raw answer with sk-raw-secret",
        )
    )

    sink.record_span(
        SpanRecord(
            name="agent.turn.sanitized",
            status="ok",
            attributes={
                "session_id": "session-1",
                "run_id": "run-1",
                "gen_ai.operation.name": "invoke_agent",
                "langfuse.observation.input": json.dumps(
                    projection["input"], sort_keys=True
                ),
                "langfuse.observation.output": json.dumps(
                    projection["output"], sort_keys=True
                ),
            },
        )
    )

    body = transport.requests[0].read().decode()
    assert "langfuse.observation.input" in body
    assert "langfuse.observation.output" in body
    assert "raw prompt" not in body
    assert "raw answer" not in body
    assert "sk-raw-secret" not in body


def test_otlp_sink_rejects_unsanitized_langfuse_observation_input() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="agent.turn.sanitized",
            status="ok",
            attributes={
                "session_id": "session-1",
                "run_id": "run-1",
                "langfuse.observation.input": "raw prompt with harmless words",
            },
        )
    )

    body = transport.requests[0].read().decode()
    assert "langfuse.observation.input" not in body
    assert "raw prompt with harmless words" not in body


def test_tool_input_shape_uses_fixed_field_list_for_langfuse(tmp_path) -> None:
    sink = RecordingSink()
    recorder = AgentObservationRecorder(
        store=JsonlAgentObservationStore(tmp_path),
        sink=sink,
    )
    recorder.start_turn(session_id="session-1", run_id="run-1", prompt="hello")

    recorder.observe_tool_call(
        tool_name="file_write",
        tool_call_id="tool-1",
        arguments={"path": "/workspace/secret.txt"},
    )
    recorder.observe_tool_result(
        tool_name="file_write",
        tool_call_id="tool-1",
        result="ok",
        is_error=False,
    )
    recorder.complete_turn(
        status="ok",
        turn=TurnTrace(user_input="hello", tool_calls=(), final_output="done"),
    )

    tool_span = _span_by_name(sink.spans, "agent.tool.sanitized")
    payload = json.loads(tool_span.attributes["langfuse.observation.input"])
    assert payload == {
        "field_count": 1,
        "fields": [{"name": "path", "type": "str", "string_length": 21}],
    }


def test_tool_input_shape_redacts_unsafe_keys_with_stable_suffixes(tmp_path) -> None:
    recorder = AgentObservationRecorder(store=JsonlAgentObservationStore(tmp_path))
    recorder.start_turn(session_id="session-1", run_id="run-1", prompt="hello")

    recorder.observe_tool_call(
        tool_name="bash_run",
        tool_call_id="tool-1",
        arguments={
            "api_key": "sk-raw-secret",
            "content": "stdout says sk-raw-secret",
            "path": "/workspace/secret.txt",
        },
    )

    body = (tmp_path / "runs" / "run-1" / "observations.jsonl").read_text(
        encoding="utf-8"
    )
    assert "api_key" not in body
    assert "content" not in body
    assert "sk-raw-secret" not in body
    assert "stdout says" not in body
    assert "/workspace/secret.txt" not in body

    records = _jsonl_records(tmp_path / "runs" / "run-1" / "observations.jsonl")
    shape = records[1]["attributes"]["arg_shape"]
    assert shape == {
        "field_count": 3,
        "fields": [
            {"name": "redacted_key", "type": "str", "string_length": 13},
            {"name": "redacted_key_2", "type": "str", "string_length": 25},
            {"name": "path", "type": "str", "string_length": 21},
        ],
    }
    assert shape["field_count"] == len(shape["fields"])


def test_otlp_sink_exports_tool_sanitized_input_field_list() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="agent.tool.sanitized",
            status="ok",
            attributes={
                "session_id": "session-1",
                "run_id": "run-1",
                "langfuse.observation.input": json.dumps(
                    {
                        "field_count": 1,
                        "fields": [
                            {"name": "path", "type": "str", "string_length": 21}
                        ],
                    },
                    sort_keys=True,
                ),
            },
        )
    )

    body = transport.requests[0].read().decode()
    assert "langfuse.observation.input" in body
    assert "fields" in body
    assert "path" in body


def test_otlp_sink_exports_redacted_tool_input_field_labels() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="agent.tool.sanitized",
            status="ok",
            attributes={
                "session_id": "session-1",
                "run_id": "run-1",
                "langfuse.observation.input": json.dumps(
                    {
                        "field_count": 2,
                        "fields": [
                            {
                                "name": "path",
                                "type": "str",
                                "string_length": 21,
                            },
                            {
                                "name": "redacted_key",
                                "type": "str",
                                "string_length": 19,
                            },
                        ],
                    },
                    sort_keys=True,
                ),
            },
        )
    )

    body = transport.requests[0].read().decode()
    assert "langfuse.observation.input" in body
    assert "redacted_key" in body


class RecordingSink:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []

    def record_span(self, span: SpanRecord) -> None:
        self.spans.append(span)

    def record_event(self, event) -> None:  # pragma: no cover - unused
        del event


class FailAfterRootSink(RecordingSink):
    def record_span(self, span: SpanRecord) -> None:
        super().record_span(span)
        if span.name == "agent.turn.sanitized":
            raise RuntimeError("sink unavailable")


def _span_by_name(spans: list[SpanRecord], name: str) -> SpanRecord:
    matches = [span for span in spans if span.name == name]
    assert len(matches) == 1, f"expected exactly one {name} span, got {len(matches)}"
    return matches[0]


def test_recorder_emits_nested_typed_spans_with_real_times(tmp_path, monkeypatch):
    clock = {"now": 1000.0}

    def fake_time() -> float:
        value = clock["now"]
        clock["now"] += 5.0
        return value

    monkeypatch.setattr("coding_agent.agent_observability.time.time", fake_time)

    store = JsonlAgentObservationStore(tmp_path)
    sink = RecordingSink()
    recorder = AgentObservationRecorder(store=store, sink=sink)

    recorder.start_turn(session_id="session-1", run_id="run-1", prompt="hello")
    recorder.observe_llm_usage(
        input_tokens=2527, output_tokens=263, provider_name="openai"
    )
    recorder.observe_tool_call(
        tool_name="web_search", tool_call_id="tool-1", arguments={"query": "rwa"}
    )
    recorder.observe_tool_result(
        tool_name="web_search", tool_call_id="tool-1", result="ok", is_error=False
    )
    recorder.complete_turn(
        status="ok",
        turn=TurnTrace(user_input="hello", tool_calls=(), final_output="done"),
    )

    turn_span = _span_by_name(sink.spans, "agent.turn.sanitized")
    generation_span = _span_by_name(sink.spans, "agent.generation.sanitized")
    tool_span = _span_by_name(sink.spans, "agent.tool.sanitized")

    # No span lands at epoch: every span has real start/end times.
    for span in (turn_span, generation_span, tool_span):
        assert span.start_time is not None
        assert span.end_time is not None
        assert span.end_time >= span.start_time

    # Turn is the trace root; children nest under it.
    assert turn_span.parent_span_id is None
    assert turn_span.span_id is not None
    assert generation_span.parent_span_id == turn_span.span_id
    assert tool_span.parent_span_id == turn_span.span_id

    # Observation types drive Langfuse rendering.
    assert turn_span.attributes["langfuse.observation.type"] == "agent"
    assert generation_span.attributes["langfuse.observation.type"] == "generation"
    assert tool_span.attributes["langfuse.observation.type"] == "tool"

    # Generation carries token usage; tool span has a non-zero duration.
    assert generation_span.attributes["gen_ai.usage.input_tokens"] == 2527
    assert generation_span.attributes["gen_ai.usage.output_tokens"] == 263
    assert tool_span.end_time > tool_span.start_time


def test_recorder_exports_turn_root_before_child_spans(tmp_path) -> None:
    store = JsonlAgentObservationStore(tmp_path)
    sink = RecordingSink()
    recorder = AgentObservationRecorder(store=store, sink=sink)

    recorder.start_turn(session_id="session-1", run_id="run-1", prompt="hello")
    recorder.observe_llm_usage(
        input_tokens=2527, output_tokens=263, provider_name="openai"
    )
    recorder.observe_tool_call(
        tool_name="web_search", tool_call_id="tool-1", arguments={"query": "rwa"}
    )
    recorder.observe_tool_result(
        tool_name="web_search", tool_call_id="tool-1", result="ok", is_error=False
    )

    assert sink.spans == []

    recorder.complete_turn(
        status="ok",
        turn=TurnTrace(user_input="hello", tool_calls=(), final_output="done"),
    )

    assert [span.name for span in sink.spans] == [
        "agent.turn.sanitized",
        "agent.generation.sanitized",
        "agent.tool.sanitized",
    ]
    turn_span = sink.spans[0]
    for child_span in sink.spans[1:]:
        assert child_span.parent_span_id == turn_span.span_id


def test_recorder_drops_pending_child_spans_after_sink_failure(tmp_path) -> None:
    store = JsonlAgentObservationStore(tmp_path)
    sink = FailAfterRootSink()
    recorder = AgentObservationRecorder(store=store, sink=sink)

    recorder.start_turn(session_id="session-1", run_id="run-1", prompt="hello")
    recorder.observe_llm_usage(
        input_tokens=2527, output_tokens=263, provider_name="openai"
    )
    recorder.complete_turn(
        status="ok",
        turn=TurnTrace(user_input="hello", tool_calls=(), final_output="done"),
    )

    sink.record_span = RecordingSink.record_span.__get__(sink, FailAfterRootSink)
    recorder.start_turn(session_id="session-1", run_id="run-2", prompt="again")
    recorder.complete_turn(
        status="ok",
        turn=TurnTrace(user_input="again", tool_calls=(), final_output="done"),
    )

    assert [span.name for span in sink.spans] == [
        "agent.turn.sanitized",
        "agent.turn.sanitized",
    ]
    assert [span.attributes["run_id"] for span in sink.spans] == ["run-1", "run-2"]


def test_otlp_payload_has_parent_span_and_nonzero_times() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="agent.tool.sanitized",
            status="ok",
            start_time=1000.0,
            end_time=1003.0,
            span_id="aaaaaaaaaaaaaaaa",
            parent_span_id="bbbbbbbbbbbbbbbb",
            attributes={"session_id": "session-1", "run_id": "run-1"},
        )
    )

    span = json.loads(transport.requests[0].read().decode())["resourceSpans"][0][
        "scopeSpans"
    ][0]["spans"][0]
    assert span["spanId"] == "aaaaaaaaaaaaaaaa"
    assert span["parentSpanId"] == "bbbbbbbbbbbbbbbb"
    assert span["startTimeUnixNano"] != "0"
    assert span["endTimeUnixNano"] != "0"


def test_otlp_sink_allows_integer_generation_token_usage() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="agent.generation.sanitized",
            status="ok",
            attributes={
                "session_id": "session-1",
                "run_id": "run-1",
                "gen_ai.usage.input_tokens": 2527,
                "gen_ai.usage.output_tokens": 263,
            },
        )
    )

    body = transport.requests[0].read().decode()
    assert "gen_ai.usage.input_tokens" in body
    assert "gen_ai.usage.output_tokens" in body
    assert "263" in body


def test_otlp_sink_rejects_non_integer_generation_token_usage() -> None:
    transport = RecordingTransport()
    sink = OtlpHttpObservationSink(
        endpoint="https://otel.example.test/v1/traces",
        client=httpx.Client(transport=httpx.MockTransport(transport.handler)),
    )

    sink.record_span(
        SpanRecord(
            name="agent.generation.sanitized",
            status="ok",
            attributes={
                "session_id": "session-1",
                "run_id": "run-1",
                "gen_ai.usage.output_tokens": "raw secret output text",
            },
        )
    )

    body = transport.requests[0].read().decode()
    assert "gen_ai.usage.output_tokens" not in body
    assert "raw secret output text" not in body
