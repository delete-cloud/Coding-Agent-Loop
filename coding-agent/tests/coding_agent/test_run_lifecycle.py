from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.runs import (
    RuntimeRunLifecycle,
    RuntimeTurnErrorHandler,
    RuntimeTurnErrorState,
    RuntimeTurnFinalizer,
    RuntimeTurnRunTracker,
    RuntimeTurnStarter,
    runtime_result_from_turn_outcome,
    runtime_status_from_turn_outcome,
)


@dataclass
class FakeSession:
    id: str
    tape_id: str | None
    provider: str = "openai"


@dataclass
class FakeResumeContext:
    previous_run_id: str


@dataclass
class FakeTurnSession:
    id: str
    tape_id: str | None
    turn_status: str = "running"
    last_failure_details: str | None = None
    runtime_message_bus: object | None = None


class FakeTape:
    def __init__(self, tape_id: str) -> None:
        self.tape_id = tape_id


class FakeRuntimeContext:
    def __init__(self, tape_id: str) -> None:
        self.tape = FakeTape(tape_id)
        self.config: dict[str, object] = {}
        self.runtime_message_bus: object | None = None


class FakeRuntimeAdapter:
    def __init__(self) -> None:
        self.consumer: object | None = None

    def set_consumer(self, consumer: object) -> None:
        self.consumer = consumer


@dataclass
class FakeRuntimeBinding:
    ctx: FakeRuntimeContext
    adapter: FakeRuntimeAdapter


class RecordingRuntimeStore:
    def __init__(self) -> None:
        self.created: list[AgentRunRecord] = []
        self.updated: list[dict[str, object]] = []

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.created.append(record)
        return record

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
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


def test_runtime_turn_outcome_helpers_map_result_and_status() -> None:
    completed = TurnOutcome(
        stop_reason=StopReason.NO_TOOL_CALLS,
        final_message="not persisted",
        steps_taken=3,
    )
    failed = TurnOutcome(stop_reason=StopReason.ERROR, error="model failed")
    interrupted = TurnOutcome(
        stop_reason=StopReason.INTERRUPTED,
        error="manual interrupt",
        steps_taken=1,
    )

    assert runtime_result_from_turn_outcome(completed) == {
        "stop_reason": "no_tool_calls",
        "steps_taken": 3,
    }
    assert runtime_status_from_turn_outcome(completed) == "completed"
    assert runtime_status_from_turn_outcome(failed) == "failed"
    assert runtime_status_from_turn_outcome(interrupted) == "interrupted"


@pytest.mark.asyncio
async def test_runtime_turn_finalizer_finishes_store_backed_outcome() -> None:
    session = FakeTurnSession(id="session-1", tape_id=None)
    ctx = FakeRuntimeContext("tape-finished")
    snapshots: list[tuple[str, str, str]] = []
    finishes: list[dict[str, Any]] = []
    persisted: list[FakeTurnSession] = []
    observations: list[tuple[str, str]] = []

    async def save_snapshot(
        session: FakeTurnSession,
        ctx: FakeRuntimeContext,
        *,
        run_id: str,
    ) -> None:
        snapshots.append((session.id, ctx.tape.tape_id, run_id))

    async def finish_run(
        session: FakeTurnSession,
        *,
        run_id: str,
        status: str,
        result: JSONObject,
        error: str | None,
        resume_context: FakeResumeContext | None = None,
    ) -> None:
        finishes.append(
            {
                "session_id": session.id,
                "run_id": run_id,
                "status": status,
                "result": result,
                "error": error,
                "resume_context": resume_context,
            }
        )

    async def persist_session(session: FakeTurnSession) -> None:
        persisted.append(session)

    finalizer = RuntimeTurnFinalizer(
        has_runtime_store=True,
        save_message_snapshot=save_snapshot,
        finish_run=finish_run,
        persist_session=persist_session,
        complete_observation=lambda *, ctx, turn_status: observations.append(
            (ctx.tape.tape_id, turn_status)
        ),
    )
    resume_context = FakeResumeContext(previous_run_id="previous-run")

    await finalizer.complete(
        session,
        ctx=ctx,
        outcome=TurnOutcome(
            stop_reason=StopReason.NO_TOOL_CALLS,
            final_message="not persisted",
            steps_taken=2,
        ),
        run_id="run-1",
        resume_context=resume_context,
    )

    assert session.tape_id == "tape-finished"
    assert session.turn_status == "running"
    assert session.last_failure_details is None
    assert snapshots == [("session-1", "tape-finished", "run-1")]
    assert finishes == [
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "status": "completed",
            "result": {"stop_reason": "no_tool_calls", "steps_taken": 2},
            "error": None,
            "resume_context": resume_context,
        }
    ]
    assert persisted == [session]
    assert observations == [("tape-finished", "completed")]


@pytest.mark.asyncio
async def test_runtime_turn_finalizer_records_storeless_failure_outcome() -> None:
    session = FakeTurnSession(id="session-1", tape_id=None)
    ctx = FakeRuntimeContext("tape-failed")
    snapshots: list[str] = []
    finishes: list[str] = []
    persisted: list[FakeTurnSession] = []
    observations: list[str] = []

    async def save_snapshot(
        session: FakeTurnSession,
        ctx: FakeRuntimeContext,
        *,
        run_id: str,
    ) -> None:
        del session, ctx
        snapshots.append(run_id)

    async def finish_run(
        session: FakeTurnSession,
        *,
        run_id: str,
        status: str,
        result: JSONObject,
        error: str | None,
        resume_context: FakeResumeContext | None = None,
    ) -> None:
        del session, status, result, error, resume_context
        finishes.append(run_id)

    async def persist_session(session: FakeTurnSession) -> None:
        persisted.append(session)

    finalizer = RuntimeTurnFinalizer(
        has_runtime_store=False,
        save_message_snapshot=save_snapshot,
        finish_run=finish_run,
        persist_session=persist_session,
        complete_observation=lambda *, ctx, turn_status: observations.append(
            turn_status
        ),
    )

    await finalizer.complete(
        session,
        ctx=ctx,
        outcome=TurnOutcome(stop_reason=StopReason.ERROR, error="provider failed"),
        run_id="run-1",
    )

    assert session.tape_id == "tape-failed"
    assert session.turn_status == "failed"
    assert session.last_failure_details == "Agent turn failed: provider failed"
    assert snapshots == []
    assert finishes == []
    assert persisted == [session]
    assert observations == ["failed"]


@pytest.mark.asyncio
async def test_runtime_turn_starter_wires_runtime_context_and_observation() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
            "resume_from": None
            if resume_context is None
            else resume_context.previous_run_id,
        },
    )
    resume_context = FakeResumeContext(previous_run_id="previous-run")
    turn_run = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        resume_context=resume_context,
    )
    message_bus = object()
    consumer = object()
    session = FakeTurnSession(
        id="session-1",
        tape_id="tape-1",
        runtime_message_bus=message_bus,
    )
    ctx = FakeRuntimeContext("tape-1")
    adapter = FakeRuntimeAdapter()
    binding = FakeRuntimeBinding(ctx=ctx, adapter=adapter)
    calls: list[tuple[str, str]] = []

    def bind_root_run_identity(
        session: FakeTurnSession,
        ctx: FakeRuntimeContext,
        run_id: str,
        *,
        resume_context: FakeResumeContext | None = None,
    ) -> None:
        del ctx
        assert resume_context is not None
        calls.append((session.id, f"root:{run_id}:{resume_context.previous_run_id}"))

    def bind_subagent_message_publisher(ctx: FakeRuntimeContext) -> None:
        calls.append(("ctx", f"publisher:{ctx.tape.tape_id}"))

    def start_observation(
        *,
        session: FakeTurnSession,
        ctx: FakeRuntimeContext,
        run_id: str,
        prompt: str,
        resume_context: FakeResumeContext | None = None,
    ) -> str:
        assert resume_context is not None
        calls.append((session.id, f"observe:{run_id}:{prompt}:{ctx.tape.tape_id}"))
        return "recorder"

    starter = RuntimeTurnStarter(
        turn_run=turn_run,
        consumer=consumer,
        run_id="run-1",
        prompt="hello",
        bind_root_run_identity=bind_root_run_identity,
        bind_subagent_message_publisher=bind_subagent_message_publisher,
        start_observation=start_observation,
        resume_context=resume_context,
    )

    recorder = await starter.start(session, binding)

    assert recorder == "recorder"
    assert adapter.consumer is consumer
    assert ctx.runtime_message_bus is message_bus
    assert ctx.config["wire_consumer"] is consumer
    assert turn_run.created is True
    assert len(store.created) == 1
    assert store.updated[0]["status"] == "running"
    assert calls == [
        ("session-1", "root:run-1:previous-run"),
        ("ctx", "publisher:tape-1"),
        ("session-1", "observe:run-1:hello:tape-1"),
    ]


@pytest.mark.asyncio
async def test_runtime_turn_error_state_marks_successful_handler_handled() -> None:
    state = RuntimeTurnErrorState()
    calls: list[str] = []

    async def handle_error() -> None:
        calls.append("handled")

    await state.handle(handle_error)

    assert calls == ["handled"]
    assert state.handled is True
    assert state.handler_failed is False


@pytest.mark.asyncio
async def test_runtime_turn_error_state_marks_handler_failure_before_reraise() -> None:
    state = RuntimeTurnErrorState()

    async def fail_error_handler() -> None:
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError, match="handler failed"):
        await state.handle(fail_error_handler)

    assert state.handled is False
    assert state.handler_failed is True


@pytest.mark.asyncio
async def test_runtime_turn_error_handler_records_generic_failure() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    tracker = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeTurnSession(id="session-1", tape_id="tape-1")
    observation_failures: list[str] = []
    closed: list[str] = []
    notifications: list[tuple[str, str]] = []

    async def close_runtime(session: FakeTurnSession) -> None:
        closed.append(session.id)

    async def notify_generic_error(
        session: FakeTurnSession,
        exc: Exception,
    ) -> None:
        notifications.append((session.id, str(exc)))

    await tracker.ensure_started(session)
    handler = RuntimeTurnErrorHandler(
        turn_run=tracker,
        close_runtime=close_runtime,
        notify_generic_error=notify_generic_error,
        fail_observation=observation_failures.append,
    )

    await handler.handle_generic(session, RuntimeError("boom"))

    assert session.turn_status == "failed"
    assert session.last_failure_details == "HTTP session turn failed: boom"
    assert observation_failures == ["RuntimeError"]
    assert closed == ["session-1"]
    assert notifications == [("session-1", "boom")]
    assert [update["status"] for update in store.updated] == ["running", "failed"]
    assert store.updated[-1]["error"] == "boom"


@pytest.mark.asyncio
async def test_runtime_turn_error_handler_records_cancel_without_closing_runtime() -> (
    None
):
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    tracker = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeTurnSession(id="session-1", tape_id="tape-1")
    cancelled: list[str] = []
    closed: list[str] = []
    notifications: list[str] = []

    async def close_runtime(session: FakeTurnSession) -> None:
        closed.append(session.id)

    async def notify_generic_error(
        session: FakeTurnSession,
        exc: Exception,
    ) -> None:
        del session
        notifications.append(str(exc))

    await tracker.ensure_started(session)
    handler = RuntimeTurnErrorHandler(
        turn_run=tracker,
        close_runtime=close_runtime,
        notify_generic_error=notify_generic_error,
        cancel_observation=lambda: cancelled.append("cancelled"),
    )

    await handler.handle_cancelled(session)

    assert cancelled == ["cancelled"]
    assert closed == []
    assert notifications == []
    assert store.updated[-1]["status"] == "cancelled"
    assert store.updated[-1]["error"] == "cancelled"


@pytest.mark.asyncio
async def test_runtime_turn_error_handler_can_start_before_generic_failure() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    tracker = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeTurnSession(id="session-1", tape_id="tape-1")

    async def close_runtime(session: FakeTurnSession) -> None:
        del session

    async def notify_generic_error(
        session: FakeTurnSession,
        exc: Exception,
    ) -> None:
        del session, exc

    handler = RuntimeTurnErrorHandler(
        turn_run=tracker,
        close_runtime=close_runtime,
        notify_generic_error=notify_generic_error,
    )

    await handler.handle_generic(
        session,
        RuntimeError("submit failed"),
        ensure_started=True,
    )

    assert tracker.created is True
    assert store.created[0].status == "queued"
    assert [update["status"] for update in store.updated] == ["running", "failed"]


@pytest.mark.asyncio
async def test_runtime_turn_run_tracker_starts_only_once() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    tracker = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeSession(id="session-1", tape_id="tape-1")

    await tracker.ensure_started(session)
    await tracker.ensure_started(session)

    assert tracker.created is True
    assert len(store.created) == 1
    assert len(store.updated) == 1
    assert store.created[0].status == "queued"
    assert store.updated[0]["status"] == "running"


@pytest.mark.asyncio
async def test_runtime_turn_run_tracker_finishes_only_when_started() -> None:
    store = RecordingRuntimeStore()
    finished_at = datetime(2026, 1, 2, tzinfo=UTC)
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
        now=lambda: finished_at,
    )
    tracker = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeSession(id="session-1", tape_id="tape-1")

    await tracker.finish_if_started(
        session,
        status="failed",
        result=cast(JSONObject, {}),
        error="before start",
    )
    await tracker.ensure_started(session)
    await tracker.finish_if_started(
        session,
        status="failed",
        result=cast(JSONObject, {"stop_reason": "error"}),
        error="after start",
    )

    assert [update["status"] for update in store.updated] == ["running", "failed"]
    assert store.updated[-1] == {
        "run_id": "run-1",
        "status": "failed",
        "ended_at": finished_at,
        "metadata": {"session_id": "session-1"},
        "result": {"stop_reason": "error"},
        "error": "after start",
    }


@pytest.mark.asyncio
async def test_runtime_run_lifecycle_skips_storeless_runs() -> None:
    metadata_calls: list[FakeSession] = []
    lifecycle = RuntimeRunLifecycle(
        store=None,
        metadata_for_session=lambda session, *, resume_context=None: (
            metadata_calls.append(session) or {}
        ),
    )

    created = await lifecycle.create(
        FakeSession(id="session-1", tape_id="tape-1"),
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert created is False
    assert metadata_calls == []


@pytest.mark.asyncio
async def test_runtime_run_lifecycle_starts_queued_then_running_run() -> None:
    store = RecordingRuntimeStore()
    metadata_calls: list[tuple[str, str | None]] = []

    def metadata_for_session(
        session: FakeSession,
        *,
        resume_context: FakeResumeContext | None = None,
    ) -> JSONObject:
        metadata_calls.append(
            (
                session.id,
                None if resume_context is None else resume_context.previous_run_id,
            )
        )
        return {
            "provider_name": session.provider,
            "resume_from": None
            if resume_context is None
            else resume_context.previous_run_id,
        }

    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=metadata_for_session,
    )
    session = FakeSession(id="session-1", tape_id="tape-1")
    resume_context = FakeResumeContext(previous_run_id="previous-run")
    started_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    created = await lifecycle.start(
        session,
        run_id="run-1",
        started_at=started_at,
        resume_context=resume_context,
    )

    assert created is True
    assert metadata_calls == [
        ("session-1", "previous-run"),
        ("session-1", "previous-run"),
    ]
    assert store.created == [
        AgentRunRecord(
            run_id="run-1",
            session_id="session-1",
            tape_id="tape-1",
            parent_run_id="previous-run",
            agent_id=None,
            status="queued",
            started_at=started_at,
            metadata={"provider_name": "openai", "resume_from": "previous-run"},
            result={},
            error=None,
        )
    ]
    assert store.updated == [
        {
            "run_id": "run-1",
            "status": "running",
            "ended_at": None,
            "metadata": {"provider_name": "openai", "resume_from": "previous-run"},
            "result": {},
            "error": None,
        }
    ]


@pytest.mark.asyncio
async def test_runtime_run_lifecycle_finishes_run_with_current_metadata() -> None:
    store = RecordingRuntimeStore()
    finished_at = datetime(2026, 1, 2, 8, 30, tzinfo=UTC)
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "provider_name": session.provider,
            "tape_id": session.tape_id,
        },
        now=lambda: finished_at,
    )
    session = FakeSession(id="session-1", tape_id="tape-finished", provider="anthropic")
    _ = await lifecycle.create(
        session,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await lifecycle.finish(
        session,
        run_id="run-1",
        status="failed",
        result=cast(JSONObject, {"steps_taken": 2}),
        error="boom",
    )

    assert store.updated == [
        {
            "run_id": "run-1",
            "status": "failed",
            "ended_at": finished_at,
            "metadata": {
                "provider_name": "anthropic",
                "tape_id": "tape-finished",
            },
            "result": {"steps_taken": 2},
            "error": "boom",
        }
    ]
