from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pytest

from agentkit.plugin.registry import PluginRegistry
from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime import AgentRunContext, ContextBudget
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.tools import FatalToolExecutionError
from agentkit.tools import ToolCallRequest, Toolset
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.__main__ import create_agent, create_child_pipeline
from coding_agent.environment import CloudCommandResult, CloudEnvironment, LocalEnvironment
from coding_agent.plugins.core_tools import CoreToolsPlugin
from coding_agent.ui.session_owner_store import SessionOwnershipConflictError
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


class CloudTraceClient:
    workspace_id: str = "ws-subagent-123"
    workspace_url: str = "https://workspace.example.com?token=secret"
    default_cwd: str = "/workspace"

    def read_file(self, path: str) -> str:
        return path

    def write_file(self, path: str, content: str) -> None:
        del path, content

    def replace_file(self, path: str, old: str, new: str) -> None:
        del path, old, new

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return []

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del pattern, directory, include
        return []

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        del patch
        return {"success": True, "path": path, "changed": False}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        del command, cwd, env, timeout
        return CloudCommandResult(stdout="", stderr="", exit_code=0)


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


@pytest.mark.asyncio
async def test_subagent_tool_publishes_timeout_summary_to_parent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        calls.append(
            {
                "session_id": session_id,
                "text": text,
                "message_id": message_id,
                "metadata": metadata,
            }
        )
        return True

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
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 0.01,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    result = await tool_fn(goal="Take too long", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent timed out after 0.01 seconds"
    assert calls == [
        {
            "session_id": "parent-session",
            "text": "Subagent timed out after 0.01 seconds",
            "message_id": None,
            "metadata": {
                "source": "subagent",
                "child_agent_id": "parent-agent.child-1",
            },
        }
    ]


@pytest.mark.asyncio
async def test_subagent_tool_publishes_completion_summary_to_parent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        calls.append(
            {
                "session_id": session_id,
                "text": text,
                "message_id": message_id,
                "metadata": metadata,
            }
        )
        return True

    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    result = await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child finished"
    assert calls == [
        {
            "session_id": "parent-session",
            "text": "Subagent completed: Child finished",
            "message_id": None,
            "metadata": {
                "source": "subagent",
                "child_agent_id": "parent-agent.child-1",
            },
        }
    ]


@pytest.mark.asyncio
async def test_subagent_tool_returns_summary_when_publisher_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del session_id, text, message_id, metadata
        raise RuntimeError("publisher unavailable")

    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )
    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    with caplog.at_level(logging.WARNING, logger="coding_agent.tools.subagent"):
        result = await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child finished"
    assert "Failed to publish subagent summary" in caplog.text


@pytest.mark.asyncio
async def test_subagent_tool_publishes_error_summary_to_parent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        calls.append(
            {
                "session_id": session_id,
                "text": text,
                "message_id": message_id,
                "metadata": metadata,
            }
        )
        return True

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class ErrorAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            return TurnOutcome(stop_reason=StopReason.ERROR, error="Child failed")

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", ErrorAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    result = await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent failed: Child failed"
    assert calls == [
        {
            "session_id": "parent-session",
            "text": "Subagent failed: Child failed",
            "message_id": None,
            "metadata": {
                "source": "subagent",
                "child_agent_id": "parent-agent.child-1",
            },
        }
    ]


@pytest.mark.asyncio
async def test_subagent_tool_publishes_interrupted_summary_to_parent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")
    events: list[str] = []

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        calls.append(
            {
                "session_id": session_id,
                "text": text,
                "message_id": message_id,
                "metadata": metadata,
            }
        )
        return True

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class InterruptedAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            return TurnOutcome(
                stop_reason=StopReason.INTERRUPTED,
                error="Cancelled by user",
            )

        async def close(self) -> None:
            events.append("adapter-close")

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", InterruptedAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    result = await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent interrupted: Cancelled by user"
    assert events == ["adapter-close"]
    assert calls == [
        {
            "session_id": "parent-session",
            "text": "Subagent interrupted: Cancelled by user",
            "message_id": None,
            "metadata": {
                "source": "subagent",
                "child_agent_id": "parent-agent.child-1",
            },
        }
    ]


@pytest.mark.asyncio
async def test_subagent_tool_publishes_run_exception_summary_to_parent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")
    events: list[str] = []

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        calls.append(
            {
                "session_id": session_id,
                "text": text,
                "message_id": message_id,
                "metadata": metadata,
            }
        )
        return True

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class RaisingAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            child_ctx.tape.append(
                Entry(
                    kind="message",
                    payload={"role": "user", "content": "Inspect"},
                )
            )
            raise RuntimeError("adapter mount failed")

        async def close(self) -> None:
            events.append("adapter-close")

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", RaisingAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_tape = Tape()
    parent_ctx = PipelineContext(
        tape=parent_tape,
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    result = await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent failed: adapter mount failed"
    assert events == ["adapter-close"]
    appended_entries = list(parent_tape)
    assert len(appended_entries) == 1
    assert appended_entries[0].meta["skip_context"] is True
    assert appended_entries[0].meta["subagent_child"] is True
    assert appended_entries[0].payload["content"] == "Inspect"
    assert calls == [
        {
            "session_id": "parent-session",
            "text": "Subagent failed: adapter mount failed",
            "message_id": None,
            "metadata": {
                "source": "subagent",
                "child_agent_id": "parent-agent.child-1",
            },
        }
    ]


@pytest.mark.asyncio
async def test_subagent_tool_publishes_adapter_close_failure_summary_to_parent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        calls.append(
            {
                "session_id": session_id,
                "text": text,
                "message_id": message_id,
                "metadata": metadata,
            }
        )
        return True

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class CloseFailingAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            child_ctx.tape.append(
                Entry(
                    kind="message",
                    payload={"role": "assistant", "content": "Child finished"},
                )
            )
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child finished",
            )

        async def close(self) -> None:
            raise RuntimeError("adapter close failed")

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", CloseFailingAdapter
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_tape = Tape()
    parent_ctx = PipelineContext(
        tape=parent_tape,
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    result = await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent failed: child adapter close failed: adapter close failed"
    appended_entries = list(parent_tape)
    assert len(appended_entries) == 1
    assert appended_entries[0].meta["skip_context"] is True
    assert appended_entries[0].meta["subagent_child"] is True
    assert appended_entries[0].payload["content"] == "Child finished"
    assert calls == [
        {
            "session_id": "parent-session",
            "text": "Subagent failed: child adapter close failed: adapter close failed",
            "message_id": None,
            "metadata": {
                "source": "subagent",
                "child_agent_id": "parent-agent.child-1",
            },
        }
    ]


def test_create_agent_injects_default_child_worker_coordinator() -> None:
    _pipeline, ctx = create_agent(session_id_override="test-session")

    coordinator = ctx.config.get("child_worker_coordinator")

    assert coordinator is not None
    assert callable(getattr(coordinator, "allocate_child_id", None))
    assert callable(getattr(coordinator, "acquire_write_lease", None))


def test_subagent_tool_schema_hides_internal_pipeline_context():
    tool_fn = build_subagent_tool(create_child_pipeline)

    params = cast(Any, tool_fn)._tool_schema.parameters

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
async def test_subagent_tool_passes_child_run_identity_to_builder(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    captured_kwargs: dict[str, Any] = {}
    environment = LocalEnvironment(tmp_path)

    class StubCoordinator:
        def allocate_child_id(self, parent_agent_id: str) -> str:
            return f"{parent_agent_id}.child-1"

    def child_pipeline_builder(**kwargs: Any):
        captured_kwargs.update(kwargs)
        return cast(Pipeline, object()), PipelineContext(
            tape=kwargs["tape_fork"],
            session_id="parent-session",
        )

    class ImmediateAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child finished",
            )

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", ImmediateAdapter)

    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        run_context=AgentRunContext(
            session_id="parent-session",
            run_id="parent-run",
            agent_id="parent-agent",
            environment=environment,
        ),
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": StubCoordinator(),
        },
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)

    result = await tool_fn(goal="Run child safely", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child finished"
    assert captured_kwargs["session_id_override"] == "parent-session"
    assert captured_kwargs["run_id_override"]
    assert captured_kwargs["run_id_override"] != "parent-run"
    assert captured_kwargs["agent_id_override"] == "parent-agent.child-1"
    assert captured_kwargs["parent_run_id_override"] == "parent-run"
    assert captured_kwargs["trace_metadata"] == {
        "subagent.parent_agent_id": "parent-agent",
        "subagent.child_agent_id": "parent-agent.child-1",
    }


def _make_immediate_adapter() -> type:
    class ImmediateAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child finished",
            )

    return ImmediateAdapter


class _StubCoordinator:
    def allocate_child_id(self, parent_agent_id: str) -> str:
        return f"{parent_agent_id}.child-1"


def _capture_kwargs_builder(captured: dict[str, Any]):
    def child_pipeline_builder(**kwargs: Any):
        captured.update(kwargs)
        return cast(Pipeline, object()), PipelineContext(
            tape=kwargs["tape_fork"],
            session_id="parent-session",
        )

    return child_pipeline_builder


@pytest.mark.asyncio
async def test_subagent_tool_forwards_parent_context_budget_and_merges_trace_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Parent budget and existing trace keys must survive into the child run."""
    captured: dict[str, Any] = {}
    environment = LocalEnvironment(tmp_path)
    parent_budget = ContextBudget(max_input_tokens=64000, reserved_output_tokens=4096)

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        run_context=AgentRunContext(
            session_id="parent-session",
            run_id="parent-run",
            agent_id="parent-agent",
            environment=environment,
            context_budget=parent_budget,
            trace_metadata={"request_id": "req-1", "tenant": "acme"},
        ),
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    tool_fn = build_subagent_tool(_capture_kwargs_builder(captured))

    await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert captured["context_budget"] is parent_budget
    assert captured["trace_metadata"] == {
        "request_id": "req-1",
        "tenant": "acme",
        "subagent.parent_agent_id": "parent-agent",
        "subagent.child_agent_id": "parent-agent.child-1",
    }


@pytest.mark.asyncio
async def test_subagent_tool_forwards_parent_environment_to_child_builder(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    captured: dict[str, Any] = {}
    environment = LocalEnvironment(tmp_path)

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        run_context=AgentRunContext(
            session_id="parent-session",
            run_id="parent-run",
            agent_id="parent-agent",
            environment=environment,
        ),
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    tool_fn = build_subagent_tool(_capture_kwargs_builder(captured))

    await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert captured["environment"] is environment


@pytest.mark.asyncio
async def test_subagent_tool_forwards_cloud_environment_to_child_builder(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}
    environment = CloudEnvironment(CloudTraceClient())

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        run_context=AgentRunContext(
            session_id="parent-session",
            run_id="parent-run",
            agent_id="parent-agent",
            environment=environment,
        ),
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    tool_fn = build_subagent_tool(_capture_kwargs_builder(captured))

    await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert captured["environment"] is environment


@pytest.mark.asyncio
async def test_subagent_tool_preserves_authoritative_cloud_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}
    environment = CloudEnvironment(CloudTraceClient())

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        run_context=AgentRunContext(
            session_id="parent-session",
            run_id="parent-run",
            agent_id="parent-agent",
            environment=environment,
            trace_metadata={
                "request_id": "req-1",
                "cloud.workspace_id": "ws-subagent-123",
            },
        ),
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    tool_fn = build_subagent_tool(_capture_kwargs_builder(captured))

    await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert captured["trace_metadata"] == {
        "request_id": "req-1",
        "cloud.workspace_id": "ws-subagent-123",
        "subagent.parent_agent_id": "parent-agent",
        "subagent.child_agent_id": "parent-agent.child-1",
    }
    assert "workspace_url" not in captured["trace_metadata"]
    assert "secret" not in str(dict(captured["trace_metadata"]))


@pytest.mark.asyncio
async def test_subagent_tool_overwrites_caller_supplied_reserved_trace_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """Caller values for the reserved subagent.* keys must be overwritten."""
    captured: dict[str, Any] = {}
    environment = LocalEnvironment(tmp_path)

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        run_context=AgentRunContext(
            session_id="parent-session",
            run_id="parent-run",
            agent_id="parent-agent",
            environment=environment,
            trace_metadata={
                "subagent.parent_agent_id": "stale-parent",
                "subagent.child_agent_id": "stale-child",
            },
        ),
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    tool_fn = build_subagent_tool(_capture_kwargs_builder(captured))

    await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert captured["trace_metadata"]["subagent.parent_agent_id"] == "parent-agent"
    assert (
        captured["trace_metadata"]["subagent.child_agent_id"]
        == "parent-agent.child-1"
    )


@pytest.mark.asyncio
async def test_subagent_tool_does_not_swallow_stale_owner_summary_publish(
    monkeypatch: pytest.MonkeyPatch,
):
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del session_id, text, message_id, metadata
        raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
        await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)


@pytest.mark.asyncio
async def test_subagent_tool_stale_owner_publish_escapes_toolset_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del session_id, text, message_id, metadata
        raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    core_tools = CoreToolsPlugin(child_pipeline_builder=child_pipeline_builder)
    registry = PluginRegistry()
    registry.register(core_tools)
    toolset = Toolset(runtime=HookRuntime(registry))
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
        await toolset.execute_tools(
            [
                ToolCallRequest(
                    tool_call_id="tc-subagent",
                    name="subagent",
                    arguments={"goal": "Inspect"},
                )
            ],
            ctx=parent_ctx,
        )


@pytest.mark.asyncio
async def test_subagent_tool_does_not_swallow_fatal_summary_publish(
    monkeypatch: pytest.MonkeyPatch,
):
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del session_id, text, message_id, metadata
        raise FatalToolExecutionError("fatal summary publish rejected")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    with pytest.raises(
        FatalToolExecutionError,
        match="fatal summary publish rejected",
    ):
        await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)


@pytest.mark.asyncio
async def test_subagent_tool_fatal_summary_publish_escapes_toolset_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    async def publish_subagent_message(
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del session_id, text, message_id, metadata
        raise FatalToolExecutionError("fatal summary publish rejected")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    core_tools = CoreToolsPlugin(child_pipeline_builder=child_pipeline_builder)
    registry = PluginRegistry()
    registry.register(core_tools)
    toolset = Toolset(runtime=HookRuntime(registry))
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
            "subagent_message_publisher": publish_subagent_message,
        },
    )

    with pytest.raises(
        FatalToolExecutionError,
        match="fatal summary publish rejected",
    ):
        await toolset.execute_tools(
            [
                ToolCallRequest(
                    tool_call_id="tc-subagent",
                    name="subagent",
                    arguments={"goal": "Inspect"},
                )
            ],
            ctx=parent_ctx,
        )


@pytest.mark.asyncio
async def test_subagent_tool_reraises_stale_owner_from_child_run_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")
    events: list[str] = []

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class StaleOwnerAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            raise SessionOwnershipConflictError("stale owner inside child run")

        async def close(self) -> None:
            events.append("adapter-close")

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", StaleOwnerAdapter
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner inside child run",
    ):
        await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert events == ["adapter-close"]


@pytest.mark.asyncio
async def test_subagent_tool_reraises_fatal_tool_execution_error_from_child_run_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")
    events: list[str] = []

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class FatalChildAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            raise FatalToolExecutionError("fatal child run rejected")

        async def close(self) -> None:
            events.append("adapter-close")

    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", FatalChildAdapter
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    with pytest.raises(
        FatalToolExecutionError,
        match="fatal child run rejected",
    ):
        await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert events == ["adapter-close"]


@pytest.mark.asyncio
async def test_subagent_tool_fails_fast_on_empty_parent_session_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """An empty parent session_id must not silently mint a fresh child uuid."""
    monkeypatch.setattr(
        "coding_agent.tools.subagent.PipelineAdapter", _make_immediate_adapter()
    )

    captured: dict[str, Any] = {}
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="",  # legacy default; no run_context attached either
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    tool_fn = build_subagent_tool(_capture_kwargs_builder(captured))

    with pytest.raises(ValueError, match="non-empty parent session_id"):
        await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)
    assert captured == {}


@pytest.mark.asyncio
async def test_subagent_tool_fails_fast_when_child_builder_omits_run_identity_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    environment = LocalEnvironment(tmp_path)

    def child_pipeline_builder(
        *,
        parent_provider: object | None,
        tape_fork: Tape,
        tool_filter,
        session_id_override: str | None = None,
    ):
        del parent_provider, tape_fork, tool_filter, session_id_override
        return cast(Pipeline, object()), PipelineContext(tape=Tape())

    class ImmediateAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("builder TypeError should stop before adapter creation")

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", ImmediateAdapter)

    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        run_context=AgentRunContext(
            session_id="parent-session",
            run_id="parent-run",
            agent_id="parent-agent",
            environment=environment,
        ),
        config={"agent_id": "parent-agent", "subagent_timeout": 30.0},
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)

    with pytest.raises(TypeError):
        await tool_fn(goal="Run child safely", __pipeline_ctx__=parent_ctx)


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


@pytest.mark.asyncio
async def test_subagent_tool_acquires_write_lease_for_mutating_child_tool_event(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    consumer = RecordingConsumer()

    class StubWriteLease:
        async def __aenter__(self) -> None:
            events.append("lease-enter")

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb
            events.append("lease-exit")

    class StubCoordinator:
        def allocate_child_id(self, parent_agent_id: str) -> str:
            return f"{parent_agent_id}.child-1" if parent_agent_id else "child-1"

        def acquire_write_lease(self) -> StubWriteLease:
            return StubWriteLease()

    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class MutatingAdapter:
        def __init__(self, **kwargs: Any) -> None:
            self._consumer = kwargs["consumer"]

        async def run_turn(self, _goal: str) -> TurnOutcome:
            child_ctx.tape.append(
                Entry(
                    kind="tool_call",
                    payload={
                        "id": "child-call-1",
                        "name": "file_write",
                        "arguments": {"path": "out.txt", "content": "x"},
                    },
                )
            )
            await self._consumer.emit(
                StreamDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    content="before write",
                )
            )
            from coding_agent.wire.protocol import ToolCallDelta, ToolResultDelta

            await self._consumer.emit(
                ToolCallDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    tool_name="file_write",
                    arguments={"path": "out.txt", "content": "x"},
                    call_id="child-call-1",
                )
            )
            await self._consumer.emit(
                ToolResultDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    call_id="child-call-1",
                    tool_name="file_write",
                    result="ok",
                )
            )
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child wrote file",
            )

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", MutatingAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "wire_consumer": consumer,
            "subagent_timeout": 30.0,
            "child_worker_coordinator": StubCoordinator(),
        },
    )

    result = await tool_fn(goal="Write a file", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child wrote file"
    assert events == ["lease-enter", "lease-exit"]


@pytest.mark.asyncio
async def test_subagent_tool_holds_write_lease_across_multiple_mutating_child_tool_events(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    consumer = RecordingConsumer()

    class StubWriteLease:
        async def __aenter__(self) -> None:
            events.append("lease-enter")

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb
            events.append("lease-exit")

    class StubCoordinator:
        def allocate_child_id(self, parent_agent_id: str) -> str:
            return f"{parent_agent_id}.child-1" if parent_agent_id else "child-1"

        def acquire_write_lease(self) -> StubWriteLease:
            return StubWriteLease()

    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class TwoWriteAdapter:
        def __init__(self, **kwargs: Any) -> None:
            self._consumer = kwargs["consumer"]

        async def run_turn(self, _goal: str) -> TurnOutcome:
            from coding_agent.wire.protocol import ToolCallDelta, ToolResultDelta

            await self._consumer.emit(
                ToolCallDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    tool_name="file_write",
                    arguments={"path": "one.txt", "content": "1"},
                    call_id="child-call-1",
                )
            )
            await self._consumer.emit(
                ToolResultDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    call_id="child-call-1",
                    tool_name="file_write",
                    result="ok",
                )
            )
            await self._consumer.emit(
                ToolCallDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    tool_name="file_write",
                    arguments={"path": "two.txt", "content": "2"},
                    call_id="child-call-2",
                )
            )
            await self._consumer.emit(
                ToolResultDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    call_id="child-call-2",
                    tool_name="file_write",
                    result="ok",
                )
            )
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child wrote two files",
            )

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", TwoWriteAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "wire_consumer": consumer,
            "subagent_timeout": 30.0,
            "child_worker_coordinator": StubCoordinator(),
        },
    )

    result = await tool_fn(goal="Write two files", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child wrote two files"
    assert events == ["lease-enter", "lease-exit"]


@pytest.mark.asyncio
async def test_subagent_tool_skips_write_lease_for_read_only_child_turn(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    class FailingLease:
        async def __aenter__(self) -> None:
            raise AssertionError("read-only child turn should not acquire write lease")

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

    class StubCoordinator:
        def allocate_child_id(self, parent_agent_id: str) -> str:
            return f"{parent_agent_id}.child-1" if parent_agent_id else "child-1"

        def acquire_write_lease(self) -> FailingLease:
            return FailingLease()

    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class ReadOnlyAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            events.append("run-turn")
            child_ctx.tape.append(
                Entry(
                    kind="tool_call",
                    payload={
                        "id": "child-call-1",
                        "name": "file_read",
                        "arguments": {"path": "out.txt"},
                    },
                )
            )
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child read file",
            )

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", ReadOnlyAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": StubCoordinator(),
        },
    )

    result = await tool_fn(goal="Read a file", __pipeline_ctx__=parent_ctx)

    assert result == "Subagent completed: Child read file"
    assert events == ["run-turn"]


@pytest.mark.asyncio
async def test_subagent_tool_releases_write_lease_and_closes_adapter_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []
    lease_acquired = asyncio.Event()

    consumer = RecordingConsumer()

    class StubWriteLease:
        async def __aenter__(self) -> None:
            events.append("lease-enter")
            lease_acquired.set()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb
            events.append("lease-exit")

    class StubCoordinator:
        def allocate_child_id(self, parent_agent_id: str) -> str:
            return f"{parent_agent_id}.child-1" if parent_agent_id else "child-1"

        def acquire_write_lease(self) -> StubWriteLease:
            return StubWriteLease()

    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class BlockingAdapter:
        def __init__(self, **kwargs: Any) -> None:
            self._consumer = kwargs["consumer"]

        async def run_turn(self, _goal: str) -> TurnOutcome:
            from coding_agent.wire.protocol import ToolCallDelta

            await self._consumer.emit(
                ToolCallDelta(
                    session_id="parent-session",
                    agent_id="parent-agent.child-1",
                    tool_name="file_write",
                    arguments={"path": "blocked.txt", "content": "x"},
                    call_id="child-call-1",
                )
            )
            await asyncio.Event().wait()
            raise AssertionError("unreachable after cancellation")

        async def close(self) -> None:
            events.append("adapter-close")

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", BlockingAdapter)

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "wire_consumer": consumer,
            "subagent_timeout": 30.0,
            "child_worker_coordinator": StubCoordinator(),
        },
    )

    task = asyncio.create_task(
        tool_fn(goal="Write then block", __pipeline_ctx__=parent_ctx)
    )
    await lease_acquired.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["lease-enter", "lease-exit", "adapter-close"]


@pytest.mark.asyncio
async def test_subagent_tool_closes_adapter_when_consumer_close_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []
    child_ctx = PipelineContext(tape=Tape(), session_id="parent-session")

    def child_pipeline_builder(**_kwargs: Any):
        return cast(Pipeline, object()), child_ctx

    class SuccessfulAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def run_turn(self, _goal: str) -> TurnOutcome:
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="Child finished",
            )

        async def close(self) -> None:
            events.append("adapter-close")

    async def cancelled_close(self) -> None:
        del self
        events.append("consumer-close")
        raise asyncio.CancelledError()

    monkeypatch.setattr("coding_agent.tools.subagent.PipelineAdapter", SuccessfulAdapter)
    monkeypatch.setattr(
        "coding_agent.tools.subagent._ChildWriteLeaseConsumer.close",
        cancelled_close,
    )

    tool_fn = build_subagent_tool(child_pipeline_builder)
    parent_ctx = PipelineContext(
        tape=Tape(),
        session_id="parent-session",
        config={
            "agent_id": "parent-agent",
            "subagent_timeout": 30.0,
            "child_worker_coordinator": _StubCoordinator(),
        },
    )

    with pytest.raises(asyncio.CancelledError):
        await tool_fn(goal="Inspect", __pipeline_ctx__=parent_ctx)

    assert events == ["consumer-close", "adapter-close"]
