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
        "safe_keys": ["cmd", "redacted_key", "count"],
        "types": {"cmd": "str", "redacted_key": "str", "count": "int"},
        "string_lengths": {"cmd": 19, "redacted_key": 13},
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
