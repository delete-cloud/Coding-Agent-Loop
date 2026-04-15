from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.__main__ import create_child_pipeline
from coding_agent.wire.protocol import StreamDelta, TurnEnd, WireMessage
from coding_agent.tools.subagent import build_subagent_tool


class ScriptedProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def model_name(self) -> str:
        return "scripted"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        **_kwargs: object,
    ):
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            assert tools is not None
            tool_names = {
                cast(dict[str, object], tool["function"])["name"]
                for tool in tools
                if isinstance(tool.get("function"), dict)
            }
            assert "subagent" not in tool_names
            yield ToolCallEvent(
                tool_call_id="child-tool-1",
                name="todo_read",
                arguments={},
            )
            yield DoneEvent()
            return

        yield TextEvent(text="Child finished summary")
        yield DoneEvent()


class RecordingConsumer:
    def __init__(self) -> None:
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req):
        from coding_agent.wire.protocol import ApprovalResponse

        return ApprovalResponse(
            session_id=req.session_id, request_id=req.request_id, approved=True
        )


@pytest.mark.asyncio
async def test_subagent_tool_runs_real_child_pipeline_and_excludes_nested_subagent():
    provider = ScriptedProvider()
    tool_fn = build_subagent_tool(create_child_pipeline)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        llm_provider=provider,
        config={"subagent_timeout": 30.0},
    )

    result = await tool_fn(
        goal="Inspect child tool availability",
        __pipeline_ctx__=parent_ctx,
    )

    assert result == "Subagent completed: Child finished summary"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_subagent_tool_child_system_prompt_explicitly_disables_nested_subagent():
    provider = ScriptedProvider()
    tool_fn = build_subagent_tool(create_child_pipeline)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        llm_provider=provider,
        config={"subagent_timeout": 30.0},
    )

    result = await tool_fn(
        goal="Inspect child tool availability",
        __pipeline_ctx__=parent_ctx,
    )

    assert result == "Subagent completed: Child finished summary"
    child_messages = provider.calls[0]["messages"]
    assert child_messages[0]["role"] == "system"
    child_system_prompt = child_messages[0]["content"]
    assert "child agent" in child_system_prompt.lower()
    assert "subagent" in child_system_prompt
    assert "unavailable" in child_system_prompt.lower()


@pytest.mark.asyncio
async def test_subagent_tool_forwards_child_agent_id_to_parent_consumer():
    provider = ScriptedProvider()
    consumer = RecordingConsumer()
    tool_fn = build_subagent_tool(create_child_pipeline)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        llm_provider=provider,
        config={"wire_consumer": consumer, "subagent_timeout": 30.0},
    )

    result = await tool_fn(
        goal="Inspect child tool availability",
        __pipeline_ctx__=parent_ctx,
    )

    assert result == "Subagent completed: Child finished summary"
    stream_deltas = [msg for msg in consumer.messages if isinstance(msg, StreamDelta)]
    assert stream_deltas
    assert all(msg.agent_id.startswith("child-") for msg in stream_deltas)
    assert all(msg.session_id == "parent-session" for msg in stream_deltas)
    turn_ends = [msg for msg in consumer.messages if isinstance(msg, TurnEnd)]
    assert turn_ends
    assert all(msg.agent_id.startswith("child-") for msg in turn_ends)


@pytest.mark.asyncio
async def test_subagent_tool_returns_timeout_summary(monkeypatch: pytest.MonkeyPatch):
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class HangingAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            await asyncio.sleep(1)
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS)

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", HangingAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={"subagent_timeout": 0.01},
    )

    result = await tool_fn(goal="Take too long", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent timed out after 0.01 seconds"


def test_subagent_tool_schema_hides_internal_pipeline_context():
    tool_fn = build_subagent_tool(create_child_pipeline)

    params = tool_fn._tool_schema.parameters

    assert params["additionalProperties"] is False
    assert set(params["properties"]) == {"goal"}
    assert params["required"] == ["goal"]


@pytest.mark.asyncio
async def test_subagent_tool_excludes_in_flight_parent_tool_calls_from_child_tape(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = ScriptedProvider()
    captured_tape: Tape | None = None
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**kwargs: Any):
        nonlocal captured_tape
        captured_tape = kwargs["tape_fork"]
        return cast(Pipeline, object()), child_ctx

    class ImmediateAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child finished",
            )

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", ImmediateAdapter)

    parent_tape = Tape(
        entries=[
            Entry(kind="message", payload={"role": "user", "content": "Try subagents"}),
            Entry(
                kind="tool_call",
                payload={
                    "id": "call-1",
                    "name": "subagent",
                    "arguments": {"goal": "child goal"},
                },
            ),
        ]
    )
    parent_ctx = PipelineContext(
        tape=parent_tape,
        session_id="parent-session",
        llm_provider=provider,
        config={"subagent_timeout": 30.0},
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)

    result = await tool_fn(goal="Run child safely", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child finished"
    assert captured_tape is not None
    captured_entries = list(captured_tape)
    assert [entry.kind for entry in captured_entries] == ["message"]
    assert captured_entries[0].payload["content"] == "Try subagents"


@pytest.mark.asyncio
async def test_subagent_tool_appends_hidden_child_trace_to_parent_tape(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = ScriptedProvider()
    child_tape: Tape | None = None

    def child_pipeline_builder(**kwargs: Any):
        nonlocal child_tape
        child_tape = kwargs["tape_fork"]
        return cast(Pipeline, object()), PipelineContext(
            tape=kwargs["tape_fork"],
            session_id="parent-session",
        )

    class ImmediateAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            assert child_tape is not None
            child_tape.append(
                Entry(
                    kind="message",
                    payload={"role": "user", "content": "Investigate child task"},
                )
            )
            child_tape.append(
                Entry(
                    kind="tool_call",
                    payload={
                        "id": "child-call-1",
                        "name": "todo_read",
                        "arguments": {},
                    },
                )
            )
            child_tape.append(
                Entry(
                    kind="tool_result",
                    payload={"tool_call_id": "child-call-1", "content": "[]"},
                )
            )
            child_tape.append(
                Entry(
                    kind="message",
                    payload={"role": "assistant", "content": "Child finished"},
                )
            )
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child finished",
            )

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", ImmediateAdapter)

    parent_tape = Tape(
        entries=[
            Entry(kind="message", payload={"role": "user", "content": "Try subagents"}),
            Entry(
                kind="tool_call",
                payload={
                    "id": "call-1",
                    "name": "subagent",
                    "arguments": {"goal": "child goal"},
                },
            ),
        ]
    )
    parent_ctx = PipelineContext(
        tape=parent_tape,
        session_id="parent-session",
        llm_provider=provider,
        config={"subagent_timeout": 30.0},
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)

    result = await tool_fn(goal="Run child safely", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child finished"
    appended_entries = list(parent_tape)[2:]
    assert [entry.kind for entry in appended_entries] == [
        "message",
        "tool_call",
        "tool_result",
        "message",
    ]
    assert all(entry.meta.get("skip_context") is True for entry in appended_entries)
    assert all(entry.meta.get("subagent_child") is True for entry in appended_entries)
    assert all(
        entry.meta.get("child_agent_id") == "child-1" for entry in appended_entries
    )
    assert all(entry.meta.get("source_tape_id") for entry in appended_entries)
