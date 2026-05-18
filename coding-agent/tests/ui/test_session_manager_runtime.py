from __future__ import annotations

import asyncio
import threading
import types
from datetime import datetime
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from agentkit.runtime.context import AgentRunContext
from agentkit.tools import FatalToolExecutionError
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.approval import ApprovalPolicy
from coding_agent.environment import (
    CloudCommandResult,
    CloudEnvironment,
    LocalEnvironment,
)
from coding_agent.runtime_store import AgentRunRecord, RuntimeEventRecord
from coding_agent.ui.binding_resolver import DefaultBindingResolver
from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    LocalExecutionBinding,
)
from coding_agent.ui.session_owner_store import SessionOwnershipConflictError
from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)
from coding_agent.ui.session_manager import (
    MockProvider,
    SessionManager,
    _load_pg_storage_types,
)
from coding_agent.ui.session_store import InMemorySessionStore
from coding_agent.ui.workspace_store import (
    JSONValue,
    WorkspaceRecord,
    WorkspaceRetentionPolicy,
    WorkspaceStatus,
)


class FakeRuntimeStore:
    def __init__(self) -> None:
        self.created: list[AgentRunRecord] = []
        self.updated: list[dict[str, object]] = []
        self.events: list[RuntimeEventRecord] = []

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.created.append(record)
        return record

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, object],
        result: dict[str, object],
        error: str | None,
    ) -> AgentRunRecord:
        self.updated.append(
            {
                "run_id": run_id,
                "status": status,
                "ended_at": ended_at,
                "metadata": metadata,
                "result": result,
                "error": error,
            }
        )
        created = self.created[-1]
        return AgentRunRecord(
            run_id=run_id,
            session_id=created.session_id,
            tape_id=created.tape_id,
            parent_run_id=created.parent_run_id,
            agent_id=created.agent_id,
            status=status,
            started_at=created.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        self.events.append(record)
        return record


@pytest.mark.asyncio
async def test_run_agent_does_not_hardcode_api_key() -> None:
    manager = SessionManager()
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())

    captured_kwargs: dict[str, object] = {}

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["session_id_override"] == session_id
    assert captured_kwargs["api_key"] is None


@pytest.mark.asyncio
async def test_run_agent_creates_run_id_and_preserves_current_turn_id_alias() -> None:
    manager = SessionManager()
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            observed_run_ids.append(self.ctx.run_context.run_id)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )
    captured_kwargs: dict[str, object] = {}
    observed_run_ids: list[str] = []

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id="stale-session",
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id="stale-session",
                run_id="stale-run",
                agent_id=None,
                parent_run_id="old-parent",
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    run_id = captured_kwargs["run_id_override"]
    assert isinstance(run_id, str)
    assert run_id
    assert captured_kwargs["session_id_override"] == session_id
    session = manager.get_session(session_id)
    assert session.current_turn_id == run_id
    assert observed_run_ids == [run_id]
    assert session.runtime_ctx.session_id == session_id
    assert session.runtime_ctx.run_context.session_id == session_id
    assert session.runtime_ctx.run_context.run_id == run_id
    assert session.runtime_ctx.run_context.parent_run_id is None


@pytest.mark.asyncio
async def test_run_agent_persists_agent_run_lifecycle_when_store_configured() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(
        provider=MockProvider(),
        provider_name="test-provider",
        model_name="test-model",
        base_url="https://user:pass@example.invalid",
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(
                stop_reason=StopReason.NO_TOOL_CALLS,
                final_message="do not persist raw final text",
                steps_taken=2,
            )

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    session = manager.get_session(session_id)
    assert len(runtime_store.created) == 1
    assert len(runtime_store.updated) == 1
    created = runtime_store.created[0]
    updated = runtime_store.updated[0]
    assert created.run_id == session.current_turn_id
    assert created.session_id == session_id
    assert created.tape_id == "stable-tape"
    assert created.status == "running"
    assert created.metadata == {
        "provider_name": "test-provider",
        "model_name": "test-model",
        "approval_policy": "auto",
        "max_steps": 30,
    }
    assert updated["run_id"] == created.run_id
    assert updated["status"] == "succeeded"
    assert updated["ended_at"] is not None
    assert updated["metadata"] == created.metadata
    assert updated["result"] == {
        "stop_reason": "no_tool_calls",
        "steps_taken": 2,
    }
    assert "final_message" not in updated["result"]
    assert updated["error"] is None


@pytest.mark.asyncio
async def test_run_agent_marks_agent_run_failed_when_turn_outcome_errors() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            return TurnOutcome(stop_reason=StopReason.ERROR, error="model failed")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert runtime_store.updated[0]["status"] == "failed"
    assert runtime_store.updated[0]["result"] == {
        "stop_reason": "error",
        "steps_taken": 0,
    }
    assert runtime_store.updated[0]["error"] == "model failed"


@pytest.mark.asyncio
async def test_run_agent_persists_wire_events_when_runtime_store_configured() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session()
    emitted_at = datetime(2026, 1, 2, 3, 4, 5)

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline
            self.ctx = ctx
            self.consumer = consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            await self.consumer.emit(
                StreamDelta(
                    session_id=session_id,
                    agent_id="root",
                    timestamp=emitted_at,
                    content="hello",
                )
            )
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")

    assert len(runtime_store.events) == 1
    event = runtime_store.events[0]
    assert event.run_id == runtime_store.created[0].run_id
    assert event.event_kind == "wire.StreamDelta"
    assert event.created_at == emitted_at
    assert event.payload == {
        "message_type": "StreamDelta",
        "message": {
            "session_id": session_id,
            "agent_id": "root",
            "timestamp": emitted_at.isoformat(),
            "content": "hello",
            "role": "assistant",
        },
    }


@pytest.mark.asyncio
async def test_run_agent_persists_approval_request_wire_events() -> None:
    runtime_store = FakeRuntimeStore()
    manager = SessionManager(runtime_store=runtime_store)
    session_id = await manager.create_session(provider=MockProvider())
    requested_at = datetime(2026, 1, 2, 3, 4, 5)
    tool_called_at = datetime(2026, 1, 2, 3, 4, 4)
    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-runtime-event",
        timestamp=requested_at,
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-runtime-event",
            timestamp=tool_called_at,
        ),
        timeout_seconds=0,
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline
            self.ctx = ctx
            self.consumer = consumer

        async def run_turn(self, prompt: str) -> TurnOutcome:
            del prompt
            await self.consumer.request_approval(req)
            return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "needs approval")

    assert len(runtime_store.events) == 1
    event = runtime_store.events[0]
    assert event.run_id == runtime_store.created[0].run_id
    assert event.event_kind == "wire.ApprovalRequest"
    assert event.created_at == requested_at
    assert event.payload == {
        "message_type": "ApprovalRequest",
        "message": {
            "session_id": session_id,
            "agent_id": "",
            "timestamp": requested_at.isoformat(),
            "request_id": "req-runtime-event",
            "tool_call": {
                "session_id": session_id,
                "agent_id": "",
                "timestamp": tool_called_at.isoformat(),
                "tool_name": "bash",
                "arguments": {"command": "pwd"},
                "call_id": "call-runtime-event",
            },
            "timeout_seconds": 0,
            "call_id": "req-runtime-event",
            "tool": "bash",
            "args": {"command": "pwd"},
            "risk_level": "low",
        },
    }


def test_load_pg_storage_types_reports_missing_optional_dependencies() -> None:
    with pytest.MonkeyPatch.context() as mp:
        fake_import_error = ModuleNotFoundError("No module named 'asyncpg'")
        mp.setattr(
            "coding_agent.ui.session_manager.importlib.import_module",
            lambda name: (_ for _ in ()).throw(fake_import_error),
        )

        with pytest.raises(RuntimeError, match="optional dependencies"):
            _load_pg_storage_types()


def test_load_pg_storage_types_reports_missing_exports() -> None:
    fake_module = types.SimpleNamespace(PGPool=object(), PGTapeStore=object())

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "coding_agent.ui.session_manager.importlib.import_module",
            lambda name: fake_module,
        )

        with pytest.raises(RuntimeError, match="PGCheckpointStore"):
            _load_pg_storage_types()


@pytest.mark.asyncio
async def test_run_agent_emits_error_turn_end_when_bootstrap_fails() -> None:
    manager = SessionManager()
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap exploded")),
        )

        await manager.run_agent(session_id, "hello")

    first = await session.wire.get_next_outgoing()
    second = await session.wire.get_next_outgoing()

    assert isinstance(first, StreamDelta)
    assert first.session_id == session_id
    assert "bootstrap exploded" in first.content

    assert isinstance(second, TurnEnd)
    assert second.session_id == session_id
    assert second.completion_status is CompletionStatus.ERROR
    assert session.turn_in_progress is False


@pytest.mark.asyncio
async def test_run_agent_clears_pending_approval_after_runtime_timeout() -> None:
    manager = SessionManager()
    session_id = await manager.create_session(provider=MockProvider())
    session = manager.get_session(session_id)

    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-timeout",
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-timeout",
        ),
        timeout_seconds=0,
    )

    runtime_consumer = None
    approval_requested = False

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, ctx
            nonlocal runtime_consumer
            runtime_consumer = consumer

        async def run_turn(self, prompt: str) -> None:
            del prompt
            nonlocal approval_requested
            assert runtime_consumer is not None
            approval_requested = True
            await runtime_consumer.request_approval(req)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (fake_pipeline, fake_ctx),
        )
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "needs approval")

    assert approval_requested is True
    assert session.pending_approval is None
    assert session.approval_response is None
    assert session.approval_store.get_request("req-timeout") is None


@pytest.mark.asyncio
async def test_run_agent_reuses_session_tape_id_across_hot_turns() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    recorded_tapes: list[Tape | None] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            self.ctx.tape.tape_id = "stable-session-tape"

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        recorded_tapes.append(kwargs.get("tape"))
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "hello")
        await manager.run_agent(session_id, "again")

    assert recorded_tapes[0] is None
    assert len(recorded_tapes) == 1
    persisted_payload = store.get(session_id)
    assert persisted_payload is not None
    assert persisted_payload["tape_id"] == "stable-session-tape"


@pytest.mark.asyncio
async def test_run_agent_reuses_live_runtime_for_hot_turns() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    create_agent_calls = 0
    adapter_instances: list[FakeAdapter] = []
    observed_prompts: list[str] = []
    observed_run_ids: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx
            adapter_instances.append(self)

        async def run_turn(self, prompt: str) -> None:
            observed_prompts.append(prompt)
            observed_run_ids.append(self.ctx.run_context.run_id)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        nonlocal create_agent_calls
        create_agent_calls += 1
        environment = kwargs["environment"]
        if not isinstance(environment, LocalEnvironment):
            raise TypeError("expected local environment")
        return fake_pipeline, types.SimpleNamespace(
            session_id=kwargs["session_id_override"],
            config={},
            tape=kwargs.get("tape") or Tape(),
            run_context=AgentRunContext(
                session_id=kwargs["session_id_override"],
                run_id=kwargs["run_id_override"],
                agent_id=None,
                environment=environment,
            ),
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        await manager.run_agent(session_id, "first")
        await manager.run_agent(session_id, "second")

    assert create_agent_calls == 1
    assert len(adapter_instances) == 1
    assert observed_prompts == ["first", "second"]
    assert len(observed_run_ids) == 2
    assert observed_run_ids[0] != observed_run_ids[1]
    assert manager.get_session(session_id).current_turn_id == observed_run_ids[1]


@pytest.mark.asyncio
async def test_run_agent_closes_cached_runtime_after_turn_failure() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise RuntimeError("turn exploded")

        async def close(self) -> None:
            close_calls.append("closed")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "boom")

    session = manager.get_session(session_id)
    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None


@pytest.mark.asyncio
async def test_run_agent_reraises_owner_conflict_without_sending_error_turn() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        async def close(self) -> None:
            close_calls.append("closed")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        with pytest.raises(
            SessionOwnershipConflictError,
            match="stale owner or fencing token rejected",
        ):
            await manager.run_agent(session_id, "boom")

    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
    assert session.turn_in_progress is False
    assert session.wire.consume_outgoing() is None


@pytest.mark.asyncio
async def test_run_agent_reraises_fatal_tool_execution_error_without_sending_error_turn() -> (
    None
):
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise FatalToolExecutionError("fatal tool failure")

        async def close(self) -> None:
            close_calls.append("closed")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        with pytest.raises(FatalToolExecutionError, match="fatal tool failure"):
            await manager.run_agent(session_id, "boom")

    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
    assert session.turn_in_progress is False
    assert session.wire.consume_outgoing() is None


@pytest.mark.asyncio
async def test_remove_session_async_awaits_runtime_close() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    close_started = asyncio.Event()
    close_released = asyncio.Event()

    class FakeAdapter:
        async def close(self) -> None:
            close_started.set()
            await close_released.wait()

    session.runtime_pipeline = object()
    session.runtime_ctx = object()
    session.runtime_adapter = FakeAdapter()

    task = asyncio.create_task(manager.remove_session_async(session_id))
    await asyncio.wait_for(close_started.wait(), timeout=1)

    assert task.done() is False
    assert manager.has_session(session_id) is True

    close_released.set()
    await task

    assert manager.has_session(session_id) is False


@pytest.mark.asyncio
async def test_close_session_raises_if_task_survives_cancellation() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    class FakeTask:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancel_calls += 1

    fake_task = FakeTask()
    session.task = cast(asyncio.Task[None], cast(object, fake_task))

    with patch(
        "coding_agent.ui.session_manager.asyncio.wait_for",
        side_effect=asyncio.TimeoutError,
    ):
        with pytest.raises(RuntimeError, match="did not stop after cancellation"):
            await manager.close_session(session_id)

    assert manager.has_session(session_id) is True
    assert fake_task.cancel_calls == 1


@pytest.mark.asyncio
async def test_shutdown_session_runtime_raises_if_task_survives_cancellation() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    class FakeTask:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancel_calls += 1

    fake_task = FakeTask()
    session.task = cast(asyncio.Task[None], cast(object, fake_task))

    with patch(
        "coding_agent.ui.session_manager.asyncio.wait_for",
        side_effect=asyncio.TimeoutError,
    ):
        with pytest.raises(RuntimeError, match="did not stop after cancellation"):
            await manager.shutdown_session_runtime(session_id)

    assert manager.has_session(session_id) is True
    assert fake_task.cancel_calls == 1


@pytest.mark.asyncio
async def test_cancel_session_turn_returns_cancelling_for_active_turn() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    task = asyncio.create_task(asyncio.sleep(60))
    session.task = task
    session.turn_in_progress = True

    result = await manager.cancel_session_turn(session_id)

    assert result.session_id == session_id
    assert result.status == "cancelling"
    assert session.as_dict()["turn_status"] == "cancelling"

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_session_turn_exposes_cancelled_final_state() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    task = asyncio.create_task(asyncio.sleep(60))
    session.task = task
    session.turn_in_progress = True

    await manager.cancel_session_turn(session_id)

    for _ in range(20):
        if session.as_dict()["turn_status"] == "cancelled":
            break
        await asyncio.sleep(0.01)

    assert session.as_dict()["turn_status"] == "cancelled"
    assert session.turn_in_progress is False
    assert session.task is None


@pytest.mark.asyncio
async def test_cancel_session_turn_is_idempotent_for_idle_session() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()

    result = await manager.cancel_session_turn(session_id)

    assert result.session_id == session_id
    assert result.status == "idle"
    assert manager.get_session(session_id).as_dict()["turn_status"] == "idle"


@pytest.mark.asyncio
async def test_register_session_closes_cached_runtime() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    close_calls: list[str] = []

    class FakeAdapter:
        async def run_turn(self, prompt: str) -> None:
            del prompt

        async def close(self) -> None:
            close_calls.append("closed")

    session.runtime_pipeline = object()
    session.runtime_ctx = object()
    session.runtime_adapter = FakeAdapter()

    manager.register_session(session)
    await asyncio.sleep(0)

    assert close_calls == ["closed"]
    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None


@pytest.mark.asyncio
async def test_run_agent_persists_tape_id_before_turn_completion() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            raise RuntimeError("turn exploded after tape allocation")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=Tape(tape_id="allocated-before-run")
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    persisted_payload = store.get(session_id)
    assert persisted_payload is not None
    assert persisted_payload["tape_id"] == "allocated-before-run"


@pytest.mark.asyncio
async def test_run_agent_rejects_concurrent_turn_for_same_session() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    started = asyncio.Event()
    release = asyncio.Event()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            started.set()
            await release.wait()

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

        first = asyncio.create_task(manager.run_agent(session_id, "first"))
        await asyncio.wait_for(started.wait(), timeout=1)

        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.run_agent(session_id, "second")

        release.set()
        await first


@pytest.mark.asyncio
async def test_prepare_session_turn_rejects_active_workspace_export() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-export/workspace",
            workspace_id="ws-export",
        )
    )
    export_started = asyncio.Event()
    release_export = threading.Event()

    def blocking_export(binding: CloudWorkspaceBinding) -> str:
        export_started.set()
        assert release_export.wait(timeout=10)
        return binding.workspace_id

    export_task = asyncio.create_task(
        manager.export_workspace_archive(session_id, blocking_export)
    )
    await asyncio.wait_for(export_started.wait(), timeout=1)

    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.prepare_session_turn(session_id)
    finally:
        release_export.set()
        await export_task


@pytest.mark.asyncio
async def test_run_agent_rejects_active_workspace_export() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-export/workspace",
            workspace_id="ws-export",
        )
    )
    export_started = asyncio.Event()
    release_export = threading.Event()

    def blocking_export(binding: CloudWorkspaceBinding) -> str:
        export_started.set()
        assert release_export.wait(timeout=10)
        return binding.workspace_id

    export_task = asyncio.create_task(
        manager.export_workspace_archive(session_id, blocking_export)
    )
    await asyncio.wait_for(export_started.wait(), timeout=1)

    try:
        with pytest.raises(RuntimeError, match="turn already in progress"):
            await manager.run_agent(session_id, "do work")
    finally:
        release_export.set()
        await export_task


@pytest.mark.asyncio
async def test_rehydrated_session_rebuilds_runtime_from_persisted_tape() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    persisted_tape = Tape(tape_id="persisted-tape")
    persisted_tape.append(
        Entry(kind="message", payload={"role": "user", "content": "before restart"})
    )

    session = manager.get_session(session_id)
    session.tape_id = "persisted-tape"
    manager.register_session(session)

    rehydrated = SessionManager(store=store)
    created_tapes: list[Tape | None] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        tape = kwargs.get("tape")
        created_tapes.append(tape)
        return fake_pipeline, types.SimpleNamespace(config={}, tape=tape)

    async def fake_restore_tape(tape_id: str):
        if tape_id == "persisted-tape":
            return persisted_tape
        return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(rehydrated, "_restore_tape", fake_restore_tape)

        await rehydrated.run_agent(session_id, "resume")

    assert created_tapes == [persisted_tape]


@pytest.mark.asyncio
async def test_failover_rebuilds_from_persisted_state_without_resuming_local_runtime() -> (
    None
):
    store = InMemorySessionStore()
    initial_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="persisted-tape"),
        plugin_states={},
    )
    rebuilt_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="persisted-tape"),
        plugin_states={},
    )
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    create_calls: list[tuple[str | None, str | None]] = []

    def create_initial_agent(**kwargs):
        tape = kwargs.get("tape")
        create_calls.append(
            (kwargs.get("session_id_override"), None if tape is None else tape.tape_id)
        )
        return fake_pipeline, initial_ctx

    def create_failover_agent(**kwargs):
        tape = kwargs.get("tape")
        create_calls.append(
            (kwargs.get("session_id_override"), None if tape is None else tape.tape_id)
        )
        return fake_pipeline, rebuilt_ctx

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
    original_task: asyncio.Task[object] | None = None

    try:
        first_manager = SessionManager(
            store=store, create_agent_fn=create_initial_agent
        )
        session_id = await first_manager.create_session()
        await first_manager.ensure_session_runtime(session_id)

        original_session = first_manager.get_session(session_id)
        original_session.turn_in_progress = True
        original_task = asyncio.create_task(asyncio.sleep(60))
        original_session.task = original_task
        original_session.event_queues.append(asyncio.Queue())
        original_session.approval_store.add_request(
            ApprovalRequest(
                session_id=session_id,
                request_id="req-local",
                tool_call=ToolCallDelta(
                    session_id=session_id,
                    tool_name="bash",
                    arguments={"command": "pwd"},
                    call_id="call-req-local",
                ),
                timeout_seconds=30,
            )
        )
        original_session.pending_approval = {
            "request_id": "req-local",
            "tool_name": "bash",
        }
        await first_manager._persist_session_async(original_session)

        second_manager = SessionManager(
            store=store, create_agent_fn=create_failover_agent
        )
        reloaded_session = second_manager.get_session(session_id)

        assert reloaded_session.runtime_pipeline is None
        assert reloaded_session.runtime_ctx is None
        assert reloaded_session.runtime_adapter is None
        assert reloaded_session.task is None
        assert reloaded_session.turn_in_progress is False
        assert reloaded_session.event_queues == []
        assert reloaded_session.pending_approval is None
        assert reloaded_session.approval_store.get_request("req-local") is None

        returned_ctx = await second_manager.ensure_session_runtime(session_id)

        assert returned_ctx is rebuilt_ctx
        assert create_calls == [
            (session_id, None),
            (session_id, "persisted-tape"),
        ]
    finally:
        monkeypatch.undo()
        if original_task is not None:
            original_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await original_task


@pytest.mark.asyncio
async def test_session_store_persists_tape_id_for_cold_recovery() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            self.ctx.tape.tape_id = "persisted-stable-id"

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape") or Tape()
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    reloaded = SessionManager(store=store).get_session(session_id)

    assert reloaded.tape_id == "persisted-stable-id"


@pytest.mark.asyncio
async def test_cold_restore_recovers_conversation_history() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "cold-tape"
    manager.register_session(session)

    restored_tape = Tape(tape_id="cold-tape")
    restored_tape.append(
        Entry(kind="message", payload={"role": "user", "content": "persisted history"})
    )
    restored_tape.append(
        Entry(
            kind="message", payload={"role": "assistant", "content": "persisted reply"}
        )
    )

    restored_entries: list[Entry] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            restored_entries.extend(list(self.ctx.tape))

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(config={}, tape=kwargs.get("tape"))

    async def fake_restore_tape(tape_id: str):
        assert tape_id == "cold-tape"
        return restored_tape

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(manager, "_restore_tape", fake_restore_tape)
        await manager.run_agent(session_id, "resume")

    assert [entry.payload["content"] for entry in restored_entries[:2]] == [
        "persisted history",
        "persisted reply",
    ]


@pytest.mark.asyncio
async def test_cold_restore_does_not_restore_live_shell_state() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "cold-shell-tape"
    manager.register_session(session)

    restored_tape = Tape(tape_id="cold-shell-tape")
    observed_plugin_states: list[dict[str, object]] = []

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            observed_plugin_states.append(dict(self.ctx.plugin_states))

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    async def fake_restore_tape(tape_id: str):
        assert tape_id == "cold-shell-tape"
        return restored_tape

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(manager, "_restore_tape", fake_restore_tape)
        await manager.run_agent(session_id, "resume")

    assert observed_plugin_states == [{}]


@pytest.mark.asyncio
async def test_restore_truncates_tape_store_to_checkpoint_entry_count() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-1",
            tape_id="checkpoint-tape",
            entry_count=2,
            window_start=0,
        ),
        tape_entries=(
            {
                "id": "e1",
                "kind": "message",
                "payload": {"role": "user", "content": "a"},
                "timestamp": 1.0,
            },
            {
                "id": "e2",
                "kind": "message",
                "payload": {"role": "assistant", "content": "b"},
                "timestamp": 2.0,
            },
        ),
        plugin_states={},
        extra={},
    )

    truncate_calls: list[tuple[str, int]] = []

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            truncate_calls.append((tape_id, keep))

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-1"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "checkpoint-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("no future checkpoints to delete")

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-1")

    assert truncate_calls == [("checkpoint-tape", 2)]


@pytest.mark.asyncio
async def test_restore_rejects_checkpoint_with_mismatched_entry_count() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-bad",
            tape_id="checkpoint-tape",
            entry_count=2,
            window_start=0,
        ),
        tape_entries=(
            {
                "id": "e1",
                "kind": "message",
                "payload": {"role": "user", "content": "a"},
                "timestamp": 1.0,
            },
        ),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-bad"
            return snapshot

        async def list(self, tape_id: str):
            raise AssertionError("should not list checkpoints for invalid snapshot")

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("should not delete checkpoints for invalid snapshot")

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            raise AssertionError("should not truncate invalid checkpoint snapshot")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("should not build runtime for invalid checkpoint")
            ),
        )

        with pytest.raises(ValueError, match="entry_count"):
            await manager._restore_checkpoint(session, "cp-bad")


@pytest.mark.asyncio
async def test_restore_injects_checkpoint_plugin_states_before_mount() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-plugin-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-plugin",
            tape_id="checkpoint-plugin-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={"topic": {"current_topic_id": "topic-1"}},
        extra={},
    )

    observed_before_mount: list[dict[str, object]] = []

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-plugin"
            return snapshot

        async def list(self, tape_id: str):
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            observed_before_mount.append(dict(self.ctx.plugin_states))

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-plugin")

    assert observed_before_mount == [{"topic": {"current_topic_id": "topic-1"}}]


@pytest.mark.asyncio
async def test_restore_rewinds_restart_safe_agent_configuration_from_checkpoint_extra() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=99,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-config-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-config",
            tape_id="checkpoint-config-tape",
            entry_count=2,
            window_start=0,
        ),
        tape_entries=(
            {
                "id": "e1",
                "kind": "message",
                "payload": {"role": "user", "content": "before config drift"},
                "timestamp": 1.0,
            },
            {
                "id": "e2",
                "kind": "message",
                "payload": {"role": "assistant", "content": "checkpoint saved"},
                "timestamp": 2.0,
            },
        ),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "checkpoint-provider",
                "model_name": "checkpoint-model",
                "base_url": "http://checkpoint.local",
                "max_steps": 7,
                "approval_policy": "interactive",
            }
        },
    )

    truncate_calls: list[tuple[str, int]] = []
    captured_kwargs: dict[str, object] = {}

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-config"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "checkpoint-config-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("no future checkpoints to delete")

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            truncate_calls.append((tape_id, keep))

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    session.provider_name = "mutated-provider"
    session.model_name = "mutated-model"
    session.base_url = "http://mutated.local"
    session.max_steps = 42
    session.approval_policy = ApprovalPolicy.YOLO

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-config")

    assert truncate_calls == [("checkpoint-config-tape", 2)]
    assert captured_kwargs["provider_override"] == "checkpoint-provider"
    assert captured_kwargs["model_override"] == "checkpoint-model"
    assert captured_kwargs["base_url_override"] == "http://checkpoint.local"
    assert captured_kwargs["max_steps_override"] == 7
    assert captured_kwargs["approval_mode_override"] == "interactive"
    assert session.provider_name == "checkpoint-provider"
    assert session.model_name == "checkpoint-model"
    assert session.base_url == "http://checkpoint.local"
    assert session.max_steps == 7
    assert session.approval_policy is ApprovalPolicy.INTERACTIVE


@pytest.mark.asyncio
async def test_run_agent_uses_resolved_workspace_root_from_binding(
    tmp_path: Path,
) -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.repo_path = tmp_path / "not-used-directly"
    bound_workspace = tmp_path / "bound-workspace"
    session.execution_binding = LocalExecutionBinding(
        workspace_root=str(bound_workspace)
    )
    manager.register_session(session)

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())
    captured_kwargs: dict[str, object] = {}

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["workspace_root"] == bound_workspace.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == bound_workspace.resolve()


@pytest.mark.asyncio
async def test_restore_checkpoint_preserves_execution_binding(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "binding-restore-tape"
    restore_bound = tmp_path / "restore-bound"
    session.execution_binding = LocalExecutionBinding(workspace_root=str(restore_bound))
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-binding",
            tape_id="binding-restore-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )
    captured_kwargs: dict[str, object] = {}

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-binding"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "binding-restore-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-binding")

    assert captured_kwargs["workspace_root"] == restore_bound.resolve()
    assert isinstance(captured_kwargs["environment"], LocalEnvironment)
    assert captured_kwargs["environment"].workspace_root == restore_bound.resolve()
    assert isinstance(session.execution_binding, LocalExecutionBinding)
    assert session.execution_binding.workspace_root == str(restore_bound)


class FakeCloudClient:
    workspace_id = "ws-123"
    workspace_url = "https://workspace.example.com"
    default_cwd = "/workspace"

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
async def test_run_agent_passes_cloud_environment_from_execution_binding() -> None:
    captured_kwargs: dict[str, object] = {}
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )
    fake_ctx = types.SimpleNamespace(config={}, tape=Tape())

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, fake_ctx

    manager = SessionManager(
        store=InMemorySessionStore(),
        create_agent_fn=fake_create_agent,
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda binding: FakeCloudClient()
        ),
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        await manager.run_agent(session_id, "hello")

    assert captured_kwargs["workspace_root"] is None
    assert isinstance(captured_kwargs["environment"], CloudEnvironment)
    assert captured_kwargs["environment"].tool_config() == {
        "workspace_id": "ws-123",
        "workspace_url": "https://workspace.example.com",
    }


@pytest.mark.asyncio
async def test_restore_checkpoint_preserves_cloud_execution_binding() -> None:
    captured_kwargs: dict[str, object] = {}
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    manager = SessionManager(
        store=InMemorySessionStore(),
        create_agent_fn=fake_create_agent,
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda binding: FakeCloudClient()
        ),
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-123",
        )
    )
    session = manager.get_session(session_id)
    session.tape_id = "cloud-restore-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-cloud-binding",
            tape_id="cloud-restore-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-cloud-binding"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "cloud-restore-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            del checkpoint_id

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            del tape_id, keep

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-cloud-binding")

    assert captured_kwargs["workspace_root"] is None
    assert isinstance(captured_kwargs["environment"], CloudEnvironment)
    assert isinstance(session.execution_binding, CloudWorkspaceBinding)
    assert session.execution_binding.workspace_id == "ws-123"


@pytest.mark.asyncio
async def test_close_session_cleans_up_server_provisioned_cloud_binding() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
    )
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-123/workspace",
        workspace_id="ws-123",
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        },
    )

    await manager.close_session(session_id)

    assert cleaned == [binding]


@pytest.mark.asyncio
async def test_close_session_offloads_provisioned_cloud_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=lambda binding: None,
    )
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-threaded/workspace",
        workspace_id="ws-threaded",
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        },
    )
    session = manager.get_session(session_id)
    to_thread_calls: list[tuple[object, tuple[object, ...]]] = []

    async def fake_to_thread(func, *args):
        to_thread_calls.append((func, args))
        return func(*args)

    monkeypatch.setattr(
        "coding_agent.ui.session_manager.asyncio.to_thread", fake_to_thread
    )

    await manager.close_session(session_id)

    assert to_thread_calls == [(manager._cleanup_provisioned_cloud_binding, (session,))]


@pytest.mark.asyncio
async def test_close_session_logs_provisioned_cloud_cleanup_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_cleanup(binding: CloudWorkspaceBinding) -> None:
        del binding
        raise RuntimeError("cleanup failed")

    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=fail_cleanup,
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-fails/workspace",
            workspace_id="ws-fails",
        ),
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        },
    )

    with caplog.at_level("ERROR"):
        await manager.close_session(session_id)

    assert not manager.has_session(session_id)
    assert "Failed to clean up provisioned cloud workspace" in caplog.text


@pytest.mark.asyncio
async def test_close_session_keeps_explicit_cloud_binding_untouched() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
    )
    session_id = await manager.create_session(
        execution_binding=CloudWorkspaceBinding(
            workspace_url="https://workspace.example.com",
            workspace_id="ws-explicit",
        ),
        origin={"channel": "http", "binding_kind": "cloud"},
    )

    await manager.close_session(session_id)

    assert cleaned == []


class FakeWorkspaceMetadataStore:
    def __init__(self, records: list[WorkspaceRecord]) -> None:
        self.records = records
        self.status_updates: list[tuple[str, WorkspaceStatus, str | None]] = []

    async def save(self, record: WorkspaceRecord) -> None:
        self.records.append(record)

    async def list(self) -> list[WorkspaceRecord]:
        return self.records

    async def load_by_workspace_id(self, workspace_id: str) -> WorkspaceRecord | None:
        for record in self.records:
            if record.workspace_id == workspace_id:
                return record
        return None

    async def load_for_session_workspace(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceRecord | None:
        for record in self.records:
            if record.session_id == session_id and record.workspace_id == workspace_id:
                return record
        return None

    async def update_status(
        self,
        workspace_record_id: str,
        *,
        status: WorkspaceStatus,
        cleanup_error: str | None = None,
    ) -> None:
        self.status_updates.append((workspace_record_id, status, cleanup_error))

    async def update_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: WorkspaceRetentionPolicy,
        expires_at: datetime | None,
        status: WorkspaceStatus,
    ) -> None:
        del workspace_record_id, retention_policy, expires_at, status

    async def update_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, JSONValue],
    ) -> None:
        del workspace_record_id, result_refs


def _workspace_record(
    *,
    session_id: str,
    workspace_id: str,
    retention_policy: WorkspaceRetentionPolicy,
) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_record_id=f"record-{workspace_id}",
        workspace_id=workspace_id,
        session_id=session_id,
        provider="docker",
        provider_instance_id="docker-host-a",
        workspace_root_ref="/workspaces",
        workspace_host_label="docker-host-a",
        owner_label="owner:test",
        source_kind="git",
        status="active",
        retention_policy=retention_policy,
    )


@pytest.mark.asyncio
async def test_create_session_persists_cloud_workspace_record() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-created/workspace",
        workspace_id="ws-created",
        runtime_profile="universal",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        workspace_metadata_store=store,
    )

    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )

    assert store.records == [
        WorkspaceRecord(
            workspace_record_id=f"{session_id}:ws-created",
            workspace_id="ws-created",
            session_id=session_id,
            provider="docker",
            provider_instance_id="docker-host-a",
            workspace_root_ref="/workspaces",
            workspace_host_label="docker-host-a",
            owner_label="owner:test",
            source_kind="docker",
            source_ref={"runtime_profile": "universal"},
            status="active",
            retention_policy="delete_on_close",
        )
    ]


@pytest.mark.asyncio
async def test_create_session_does_not_persist_explicit_cloud_binding_record() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="docker://external-workspace/workspace",
        workspace_id="external-workspace",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        workspace_metadata_store=store,
    )

    await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
        },
    )

    assert store.records == []


@pytest.mark.asyncio
async def test_close_session_retains_workspace_when_policy_is_pinned() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-pinned/workspace",
        workspace_id="ws-pinned",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="pinned",
    )

    await manager.close_session(session_id)

    assert cleaned == []
    assert store.status_updates == [("record-ws-pinned", "retained", None)]


@pytest.mark.asyncio
async def test_close_session_deletes_workspace_when_policy_is_delete_on_close() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-delete/workspace",
        workspace_id="ws-delete",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="delete_on_close",
    )

    await manager.close_session(session_id)

    assert cleaned == [binding]
    assert store.status_updates == [("record-ws-delete", "cleaned", None)]


@pytest.mark.asyncio
async def test_close_session_marks_workspace_cleanup_failed() -> None:
    def fail_cleanup(binding: CloudWorkspaceBinding) -> None:
        del binding
        raise RuntimeError("cleanup failed")

    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-cleanup-failed/workspace",
        workspace_id="ws-cleanup-failed",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=fail_cleanup,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="delete_on_close",
    )

    await manager.close_session(session_id)

    assert store.status_updates == [
        (
            "record-ws-cleanup-failed",
            "cleanup_failed",
            "cleanup failed",
        )
    ]


@pytest.mark.asyncio
async def test_shutdown_session_runtime_does_not_apply_workspace_retention() -> None:
    cleaned: list[CloudWorkspaceBinding] = []
    binding = CloudWorkspaceBinding(
        workspace_url="docker://agent-ws-shutdown/workspace",
        workspace_id="ws-shutdown",
    )
    store = FakeWorkspaceMetadataStore([])
    manager = SessionManager(
        store=InMemorySessionStore(),
        provisioned_cloud_binding_cleanup=cleaned.append,
        workspace_metadata_store=store,
    )
    session_id = await manager.create_session(
        execution_binding=binding,
        origin={
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-host-a",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "docker-host-a",
            "owner_label": "owner:test",
        },
    )
    store.records[0] = _workspace_record(
        session_id=session_id,
        workspace_id=binding.workspace_id,
        retention_policy="delete_on_close",
    )

    await manager.shutdown_session_runtime(session_id)

    assert cleaned == []
    assert store.status_updates == []
    assert manager.has_session(session_id)


@pytest.mark.asyncio
async def test_restore_legacy_checkpoint_without_session_config_uses_current_session_metadata() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=11,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "legacy-checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-legacy",
            tape_id="legacy-checkpoint-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    captured_kwargs: dict[str, object] = {}

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-legacy"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "legacy-checkpoint-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-legacy")

    assert captured_kwargs["provider_override"] == "current-provider"
    assert captured_kwargs["model_override"] == "current-model"
    assert captured_kwargs["base_url_override"] == "http://current.local"
    assert captured_kwargs["max_steps_override"] == 11
    assert captured_kwargs["approval_mode_override"] == "auto"


@pytest.mark.asyncio
async def test_restore_rejects_partial_checkpoint_session_config_payload() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session(
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=11,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "invalid-checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-invalid",
            tape_id="invalid-checkpoint-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "checkpoint-provider",
                "approval_policy": "interactive",
            }
        },
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-invalid"
            return snapshot

        async def list(self, tape_id: str):
            raise AssertionError("invalid checkpoint config should fail early")

        async def delete(self, checkpoint_id: str) -> None:
            raise AssertionError("invalid checkpoint config should fail early")

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            raise AssertionError("invalid checkpoint config should not truncate")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        mp.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("invalid checkpoint config should not build runtime")
            ),
        )

        with pytest.raises(TypeError, match="missing .*model_name"):
            await manager._restore_checkpoint(session, "cp-invalid")


@pytest.mark.asyncio
async def test_restore_clears_hot_provider_override_when_checkpoint_rewinds_provider_metadata() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    current_provider = MockProvider()
    session_id = await manager.create_session(
        provider=current_provider,
        provider_name="current-provider",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=13,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "hot-provider-checkpoint-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-hot-provider",
            tape_id="hot-provider-checkpoint-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "checkpoint-provider",
                "model_name": "checkpoint-model",
                "base_url": "http://checkpoint.local",
                "max_steps": 5,
                "approval_policy": "interactive",
            }
        },
    )

    llm_plugin = types.SimpleNamespace(_instance=None)

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-hot-provider"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "hot-provider-checkpoint-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(get=lambda _: llm_plugin)
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-hot-provider")

    assert llm_plugin._instance is None
    assert session.provider is None


@pytest.mark.asyncio
async def test_restore_does_not_reuse_hot_provider_when_model_changes_with_same_provider() -> (
    None
):
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    hot_provider = MockProvider()
    hot_provider._model_name = "current-model"

    session_id = await manager.create_session(
        provider=hot_provider,
        provider_name="openai",
        model_name="current-model",
        base_url="http://current.local",
        max_steps=13,
        approval_policy=ApprovalPolicy.AUTO,
    )
    session = manager.get_session(session_id)
    session.tape_id = "same-provider-different-model-tape"
    manager.register_session(session)

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-same-provider-new-model",
            tape_id="same-provider-different-model-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={
            "session_restart_config": {
                "provider_name": "openai",
                "model_name": "rewound-model",
                "base_url": "http://current.local",
                "max_steps": 5,
                "approval_policy": "interactive",
            }
        },
    )

    llm_plugin = types.SimpleNamespace(_instance=None)

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-same-provider-new-model"
            return snapshot

        async def list(self, tape_id: str):
            assert tape_id == "same-provider-different-model-tape"
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(get=lambda _: llm_plugin)
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={}, tape=kwargs.get("tape"), plugin_states={}
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-same-provider-new-model")

    assert llm_plugin._instance is None
    assert session.provider is None


@pytest.mark.asyncio
async def test_restore_closes_existing_runtime_before_replacing_it() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.tape_id = "checkpoint-plugin-tape"
    manager.register_session(session)

    close_calls: list[str] = []

    class ExistingAdapter:
        async def run_turn(self, prompt: str) -> None:
            del prompt

        async def close(self) -> None:
            close_calls.append("old-runtime")

    session.runtime_pipeline = object()
    session.runtime_ctx = object()
    session.runtime_adapter = ExistingAdapter()

    snapshot = types.SimpleNamespace(
        meta=types.SimpleNamespace(
            checkpoint_id="cp-plugin",
            tape_id="checkpoint-plugin-tape",
            entry_count=0,
            window_start=0,
        ),
        tape_entries=(),
        plugin_states={},
        extra={},
    )

    class FakeCheckpointService:
        async def restore(self, checkpoint_id: str):
            assert checkpoint_id == "cp-plugin"
            return snapshot

        async def list(self, tape_id: str):
            return [snapshot.meta]

        async def delete(self, checkpoint_id: str) -> None:
            return None

    class FakeTapeStore:
        async def truncate(self, tape_id: str, keep: int) -> None:
            return None

    class NewAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            close_calls.append("new-runtime")

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs):
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=kwargs.get("tape"),
            plugin_states={},
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("coding_agent.__main__.create_agent", fake_create_agent)
        mp.setattr("coding_agent.ui.session_manager.PipelineAdapter", NewAdapter)
        mp.setattr(
            manager, "_checkpoint_service", FakeCheckpointService(), raising=False
        )
        mp.setattr(manager, "_tape_store", FakeTapeStore(), raising=False)
        await manager._restore_checkpoint(session, "cp-plugin")

    assert close_calls == ["old-runtime"]
    assert isinstance(session.runtime_adapter, NewAdapter)


def test_clear_sessions_clears_session_turn_locks() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    _ = manager._turn_lock_for("session-a")
    _ = manager._turn_lock_for("session-b")

    assert manager._session_turn_locks

    manager.clear_sessions()

    assert manager._session_turn_locks == {}


@pytest.mark.asyncio
async def test_clear_sessions_closes_cached_runtimes() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    first = manager.get_session(await manager.create_session())
    second = manager.get_session(await manager.create_session())
    close_calls: list[str] = []

    class FakeAdapter:
        def __init__(self, name: str) -> None:
            self.name = name

        async def run_turn(self, prompt: str) -> None:
            del prompt

        async def close(self) -> None:
            close_calls.append(self.name)

    first.runtime_pipeline = object()
    first.runtime_ctx = object()
    first.runtime_adapter = FakeAdapter("first")
    second.runtime_pipeline = object()
    second.runtime_ctx = object()
    second.runtime_adapter = FakeAdapter("second")

    manager.clear_sessions()
    await asyncio.sleep(0)

    assert close_calls == ["first", "second"]
