from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from coding_agent.adapter.types import (
    StopReason,
    TurnOutcome,
    exception_error_message,
)
from coding_agent.stores.runtime_store import (
    AgentRunRecord,
    JSONObject,
    SQLiteRuntimeStore,
)
from coding_agent.runs import (
    RuntimeCloser,
    RuntimeRunLifecycle,
    RuntimeRunPersistenceService,
    RuntimeTaskStopper,
    RuntimeTurnController,
    RuntimeTurnSessionState,
    RuntimeTurnObservationState,
    RuntimeTurnErrorHandler,
    RuntimeTurnErrorState,
    RuntimeTurnFinalizer,
    RuntimeTurnRunTracker,
    RuntimeTurnStarter,
    runtime_result_from_turn_outcome,
    runtime_status_from_turn_outcome,
)
from coding_agent.topics.context_pack import (
    ContextPack,
    ContextPackItem,
    ContextPackSection,
    stash_context_pack,
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


@dataclass
class FakeTurnStateSession:
    id: str
    tape_id: str | None
    last_activity: datetime
    turn_in_progress: bool = False
    turn_status: str = "idle"
    current_turn_id: str | None = None
    last_failure_details: str | None = "previous failure"
    task: object | None = None


@dataclass
class FakeRuntimeHandleSession:
    runtime_pipeline: object | None = None
    runtime_ctx: object | None = None
    runtime_adapter: object | None = None

    def detach_runtime_adapter(self) -> object | None:
        adapter = self.runtime_adapter
        self.runtime_pipeline = None
        self.runtime_ctx = None
        self.runtime_adapter = None
        return adapter


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


class FakeObservationRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def fail_turn(self, *, error_type: str) -> None:
        self.events.append(("fail", error_type))

    def cancel_turn(self) -> None:
        self.events.append(("cancel", ""))


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
async def test_runtime_closer_invalidates_runtime_before_awaiting_adapter_close() -> (
    None
):
    close_started = asyncio.Event()
    close_released = asyncio.Event()
    session = FakeRuntimeHandleSession(
        runtime_pipeline=object(),
        runtime_ctx=object(),
    )

    class BlockingAdapter:
        async def close(self) -> None:
            close_started.set()
            await close_released.wait()

    session.runtime_adapter = BlockingAdapter()
    close_task = asyncio.create_task(RuntimeCloser().close(session))
    await asyncio.wait_for(close_started.wait(), timeout=1)

    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
    assert close_task.done() is False

    close_released.set()
    await close_task


@pytest.mark.asyncio
async def test_runtime_closer_propagates_async_close_failure_after_invalidating() -> (
    None
):
    session = FakeRuntimeHandleSession(
        runtime_pipeline=object(),
        runtime_ctx=object(),
    )

    class FailingAdapter:
        async def close(self) -> None:
            raise RuntimeError("close exploded")

    session.runtime_adapter = FailingAdapter()

    with pytest.raises(RuntimeError, match="close exploded"):
        await RuntimeCloser().close(session)

    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None


@pytest.mark.asyncio
async def test_runtime_closer_sync_safe_schedules_close_on_running_loop() -> None:
    closed = asyncio.Event()
    session = FakeRuntimeHandleSession(
        runtime_pipeline=object(),
        runtime_ctx=object(),
    )

    class AsyncAdapter:
        async def close(self) -> None:
            closed.set()

    session.runtime_adapter = AsyncAdapter()

    RuntimeCloser().close_sync_safe(session)

    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
    await asyncio.wait_for(closed.wait(), timeout=1)


def test_runtime_closer_sync_safe_closes_without_running_loop() -> None:
    closed: list[str] = []
    session = FakeRuntimeHandleSession(
        runtime_pipeline=object(),
        runtime_ctx=object(),
    )

    class AsyncAdapter:
        async def close(self) -> None:
            closed.append("closed")

    session.runtime_adapter = AsyncAdapter()

    RuntimeCloser().close_sync_safe(session)

    assert session.runtime_pipeline is None
    assert session.runtime_ctx is None
    assert session.runtime_adapter is None
    assert closed == ["closed"]


@pytest.mark.asyncio
async def test_runtime_task_stopper_noops_without_active_task() -> None:
    await RuntimeTaskStopper().stop(session_id="session-1", task=None)


@pytest.mark.asyncio
async def test_runtime_task_stopper_cancels_active_task() -> None:
    task = asyncio.create_task(asyncio.sleep(60))

    await RuntimeTaskStopper().stop(session_id="session-1", task=task)

    assert task.cancelled() is True


@pytest.mark.asyncio
async def test_runtime_task_stopper_raises_when_task_survives_cancellation() -> None:
    class FakeTask:
        def __init__(self) -> None:
            self.cancel_calls = 0

        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            self.cancel_calls += 1

    fake_task = FakeTask()

    with pytest.MonkeyPatch.context() as monkeypatch:

        async def timeout_wait_for(task: object, *, timeout: float) -> None:
            assert task is fake_task
            assert timeout == 0.01
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", timeout_wait_for)
        with pytest.raises(RuntimeError, match="did not stop after cancellation"):
            await RuntimeTaskStopper(timeout=0.01).stop(
                session_id="session-1",
                task=cast(Any, fake_task),
            )

    assert fake_task.cancel_calls == 1


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
        extra_metadata: JSONObject | None = None,
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
        extra_metadata: JSONObject | None = None,
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
async def test_runtime_turn_finalizer_persists_stashed_context_pack() -> None:
    store = RecordingRuntimeStore()
    persistence = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=None,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    persisted: list[FakeTurnSession] = []

    async def persist_session(session: FakeTurnSession) -> None:
        persisted.append(session)

    finalizer = persistence.turn_finalizer(persist_session=persist_session)
    session = FakeTurnSession(id="session-1", tape_id=None)
    ctx = FakeRuntimeContext("tape-recalled")
    stash_context_pack(
        ctx.config,
        contributor="semantic_memory",
        pack=ContextPack(
            sections=(
                ContextPackSection(
                    title="Cross-topic recall references",
                    items=(
                        ContextPackItem(
                            source_kind="topic_summary",
                            source_id="topic:topic-auth",
                            label="Auth recall",
                            score=0.47,
                            score_scale="similarity",
                        ),
                    ),
                ),
            )
        ),
    )

    await persistence.lifecycle().start(
        session,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await finalizer.complete(
        session,
        ctx=ctx,
        outcome=TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1),
        run_id="run-1",
    )

    metadata = store.updated[-1]["metadata"]
    assert metadata["session_id"] == "session-1"
    pack = metadata["context_pack"]
    assert pack["title"] == "Context Pack"
    item = pack["sections"][0]["items"][0]
    assert item["source_kind"] == "topic_summary"
    assert item["source_id"] == "topic:topic-auth"
    assert item["label"] == "Auth recall"
    assert item["score"] == 0.47
    assert item["score_scale"] == "similarity"


@pytest.mark.asyncio
async def test_runtime_turn_finalizer_sanitizes_non_finite_context_pack_scores() -> (
    None
):
    store = RecordingRuntimeStore()
    persistence = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=None,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )

    async def persist_session(session: FakeTurnSession) -> None:
        del session

    finalizer = persistence.turn_finalizer(persist_session=persist_session)
    session = FakeTurnSession(id="session-1", tape_id=None)
    ctx = FakeRuntimeContext("tape-non-finite")
    stash_context_pack(
        ctx.config,
        contributor="semantic_memory",
        pack=ContextPack(
            sections=(
                ContextPackSection(
                    title="Cross-topic recall references",
                    items=(
                        ContextPackItem(
                            source_kind="memory",
                            source_id="memory:nan",
                            label="NaN score",
                            score=float("nan"),
                        ),
                        ContextPackItem(
                            source_kind="memory",
                            source_id="memory:inf",
                            label="Inf score",
                            score=float("inf"),
                        ),
                        ContextPackItem(
                            source_kind="memory",
                            source_id="memory:finite",
                            label="Finite score",
                            score=0.5,
                        ),
                    ),
                ),
            )
        ),
    )

    await persistence.lifecycle().start(
        session,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await finalizer.complete(
        session,
        ctx=ctx,
        outcome=TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1),
        run_id="run-1",
    )

    metadata = store.updated[-1]["metadata"]
    items = metadata["context_pack"]["sections"][0]["items"]
    scores = {item["source_id"]: item["score"] for item in items}
    assert scores == {
        "memory:nan": "nan",
        "memory:inf": "inf",
        "memory:finite": 0.5,
    }
    # Starlette JSONResponse serializes with allow_nan=False; this must not raise.
    json.dumps(metadata, allow_nan=False)


@pytest.mark.asyncio
async def test_runtime_turn_finalizer_omits_context_pack_without_stash() -> None:
    store = RecordingRuntimeStore()
    persistence = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=None,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )

    async def persist_session(session: FakeTurnSession) -> None:
        del session

    finalizer = persistence.turn_finalizer(persist_session=persist_session)
    session = FakeTurnSession(id="session-1", tape_id=None)

    await persistence.lifecycle().start(
        session,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await finalizer.complete(
        session,
        ctx=FakeRuntimeContext("tape-plain"),
        outcome=TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1),
        run_id="run-1",
    )

    assert "context_pack" not in store.updated[-1]["metadata"]


def test_runtime_turn_observation_state_tracks_recorder_actions() -> None:
    recorder = FakeObservationRecorder()
    ctx = FakeRuntimeContext("tape-observed")
    completions: list[tuple[object | None, str, str]] = []

    state = RuntimeTurnObservationState(
        complete_observation=lambda recorder, *, ctx, turn_status: completions.append(
            (recorder, ctx.tape.tape_id, turn_status)
        )
    )

    state.set(recorder)
    state.complete(ctx=ctx, turn_status="completed")
    state.fail("RuntimeError")
    state.cancel()

    assert completions == [(recorder, "tape-observed", "completed")]
    assert recorder.events == [("fail", "RuntimeError"), ("cancel", "")]


def test_runtime_turn_observation_state_ignores_missing_recorder() -> None:
    ctx = FakeRuntimeContext("tape-missing")
    completions: list[tuple[object | None, str, str]] = []

    state = RuntimeTurnObservationState(
        complete_observation=lambda recorder, *, ctx, turn_status: completions.append(
            (recorder, ctx.tape.tape_id, turn_status)
        )
    )

    state.complete(ctx=ctx, turn_status="completed")
    state.fail("RuntimeError")
    state.cancel()

    assert completions == [(None, "tape-missing", "completed")]
    assert state.recorder is None


@pytest.mark.asyncio
async def test_runtime_turn_session_state_begins_turn_and_persists() -> None:
    times = iter(
        [
            datetime(2026, 1, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, 2, tzinfo=UTC),
        ]
    )
    persisted: list[FakeTurnStateSession] = []
    session = FakeTurnStateSession(
        id="session-1",
        tape_id="tape-1",
        last_activity=datetime(2025, 1, 1, tzinfo=UTC),
    )

    async def persist_session(session: FakeTurnStateSession) -> None:
        persisted.append(session)

    state = RuntimeTurnSessionState(
        persist_session=persist_session,
        now=lambda: next(times),
    )

    started_at = await state.begin(session, run_id="run-1")

    assert started_at == datetime(2026, 1, 1, 2, tzinfo=UTC)
    assert session.last_activity == datetime(2026, 1, 1, 1, tzinfo=UTC)
    assert session.turn_in_progress is True
    assert session.turn_status == "running"
    assert session.current_turn_id == "run-1"
    assert session.last_failure_details is None
    assert persisted == [session]


@pytest.mark.asyncio
async def test_runtime_turn_session_state_finalizes_detached_running_turn() -> None:
    persisted: list[FakeTurnStateSession] = []
    session = FakeTurnStateSession(
        id="session-1",
        tape_id="tape-1",
        last_activity=datetime(2025, 1, 1, tzinfo=UTC),
        turn_in_progress=True,
        turn_status="running",
        task=object(),
    )

    async def persist_session(session: FakeTurnStateSession) -> None:
        persisted.append(session)

    state = RuntimeTurnSessionState(
        persist_session=persist_session,
        now=lambda: datetime(2026, 1, 2, tzinfo=UTC),
    )

    await state.finalize(session, current_task=object())

    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.last_activity == datetime(2026, 1, 2, tzinfo=UTC)
    assert persisted == [session]


@pytest.mark.asyncio
async def test_runtime_turn_session_state_preserves_owned_running_turn() -> None:
    current_task = object()
    persisted: list[FakeTurnStateSession] = []
    session = FakeTurnStateSession(
        id="session-1",
        tape_id="tape-1",
        last_activity=datetime(2025, 1, 1, tzinfo=UTC),
        turn_in_progress=True,
        turn_status="running",
        task=current_task,
    )

    async def persist_session(session: FakeTurnStateSession) -> None:
        persisted.append(session)

    state = RuntimeTurnSessionState(
        persist_session=persist_session,
        now=lambda: datetime(2026, 1, 2, tzinfo=UTC),
    )

    await state.finalize(session, current_task=current_task)

    assert session.turn_in_progress is True
    assert session.turn_status == "running"
    assert session.last_activity == datetime(2026, 1, 2, tzinfo=UTC)
    assert persisted == [session]


@pytest.mark.asyncio
async def test_runtime_turn_session_state_finalizes_owned_finished_turn() -> None:
    current_task = object()
    persisted: list[FakeTurnStateSession] = []
    session = FakeTurnStateSession(
        id="session-1",
        tape_id="tape-1",
        last_activity=datetime(2025, 1, 1, tzinfo=UTC),
        turn_in_progress=True,
        turn_status="running",
        task=current_task,
    )

    async def persist_session(session: FakeTurnStateSession) -> None:
        persisted.append(session)

    state = RuntimeTurnSessionState(
        persist_session=persist_session,
        now=lambda: datetime(2026, 1, 2, tzinfo=UTC),
    )

    await state.finalize(
        session,
        current_task=current_task,
        turn_finished=True,
    )

    assert session.task is None
    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.last_activity == datetime(2026, 1, 2, tzinfo=UTC)
    assert persisted == [session]


@pytest.mark.asyncio
async def test_runtime_turn_session_state_finalizes_owned_done_task() -> None:
    current_task = asyncio.create_task(asyncio.sleep(0))
    await current_task
    persisted: list[FakeTurnStateSession] = []
    session = FakeTurnStateSession(
        id="session-1",
        tape_id="tape-1",
        last_activity=datetime(2025, 1, 1, tzinfo=UTC),
        turn_in_progress=True,
        turn_status="running",
        task=current_task,
    )

    async def persist_session(session: FakeTurnStateSession) -> None:
        persisted.append(session)

    state = RuntimeTurnSessionState(
        persist_session=persist_session,
        now=lambda: datetime(2026, 1, 2, tzinfo=UTC),
    )

    await state.finalize(session, current_task=current_task)

    assert session.task is None
    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.last_activity == datetime(2026, 1, 2, tzinfo=UTC)
    assert persisted == [session]


@pytest.mark.asyncio
async def test_runtime_turn_controller_routes_before_and_after_hooks() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    turn_run = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    recorder = FakeObservationRecorder()
    completions: list[tuple[object | None, str, str]] = []
    persisted: list[FakeTurnSession] = []
    snapshots: list[str] = []
    finishes: list[str] = []

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
        extra_metadata: JSONObject | None = None,
    ) -> None:
        del session, status, result, error, resume_context
        finishes.append(run_id)

    async def persist_session(session: FakeTurnSession) -> None:
        persisted.append(session)

    observation = RuntimeTurnObservationState(
        complete_observation=lambda recorder, *, ctx, turn_status: completions.append(
            (recorder, ctx.tape.tape_id, turn_status)
        )
    )
    controller = RuntimeTurnController(
        starter=RuntimeTurnStarter(
            turn_run=turn_run,
            consumer=consumer,
            run_id="run-1",
            prompt="hello",
            bind_root_run_identity=lambda session, ctx, run_id, *, resume_context=None: (
                None
            ),
            bind_subagent_message_publisher=lambda ctx: None,
            start_observation=lambda **kwargs: recorder,
        ),
        finalizer=RuntimeTurnFinalizer(
            has_runtime_store=True,
            save_message_snapshot=save_snapshot,
            finish_run=finish_run,
            persist_session=persist_session,
            complete_observation=observation.complete,
        ),
        observation=observation,
        error_handler=RuntimeTurnErrorHandler(
            turn_run=turn_run,
            close_runtime=lambda session: persist_session(session),
            notify_generic_error=lambda session, exc: persist_session(session),
        ),
    )

    await controller.before_turn(session, binding)
    await controller.after_turn(
        session,
        binding,
        TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=1),
    )

    assert adapter.consumer is consumer
    assert ctx.runtime_message_bus is message_bus
    assert ctx.config["wire_consumer"] is consumer
    assert store.updated[0]["status"] == "running"
    assert snapshots == ["run-1"]
    assert finishes == ["run-1"]
    assert persisted == [session]
    assert completions == [(recorder, "tape-1", "completed")]


@pytest.mark.asyncio
async def test_runtime_turn_controller_does_not_double_handle_inner_error() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    turn_run = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeTurnSession(id="session-1", tape_id="tape-1")
    failures: list[str] = []
    notifications: list[str] = []
    closed: list[str] = []

    async def close_runtime(session: FakeTurnSession) -> None:
        closed.append(session.id)

    async def notify_generic_error(
        session: FakeTurnSession,
        exc: Exception,
    ) -> None:
        del session
        notifications.append(str(exc))

    await turn_run.ensure_started(session)
    controller = RuntimeTurnController(
        starter=None,
        finalizer=None,
        error_handler=RuntimeTurnErrorHandler(
            turn_run=turn_run,
            close_runtime=close_runtime,
            notify_generic_error=notify_generic_error,
            fail_observation=failures.append,
        ),
    )
    exc = RuntimeError("boom")

    await controller.on_turn_error(session, exc)
    should_reraise = await controller.handle_outer_exception(session, exc)

    assert should_reraise is False
    assert failures == ["RuntimeError"]
    assert notifications == ["boom"]
    assert closed == ["session-1"]
    assert [update["status"] for update in store.updated] == ["running", "failed"]


@pytest.mark.asyncio
async def test_runtime_turn_controller_run_execution_routes_generic_error() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    turn_run = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeTurnSession(id="session-1", tape_id="tape-1")
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

    await turn_run.ensure_started(session)
    controller = RuntimeTurnController(
        error_handler=RuntimeTurnErrorHandler(
            turn_run=turn_run,
            close_runtime=close_runtime,
            notify_generic_error=notify_generic_error,
        ),
    )

    async def execute() -> None:
        raise RuntimeError("boom")

    await controller.run_execution(session, execute)

    assert session.turn_status == "failed"
    assert closed == ["session-1"]
    assert notifications == ["boom"]
    assert store.updated[-1]["status"] == "failed"
    assert store.updated[-1]["error"] == "boom"


@pytest.mark.asyncio
async def test_runtime_turn_controller_run_execution_reraises_fatal_error() -> None:
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    turn_run = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeTurnSession(id="session-1", tape_id="tape-1")
    closed: list[str] = []

    async def close_runtime(session: FakeTurnSession) -> None:
        closed.append(session.id)

    async def notify_generic_error(
        session: FakeTurnSession,
        exc: Exception,
    ) -> None:
        raise AssertionError(f"fatal errors should not notify generic error: {exc!r}")

    await turn_run.ensure_started(session)
    controller = RuntimeTurnController(
        error_handler=RuntimeTurnErrorHandler(
            turn_run=turn_run,
            close_runtime=close_runtime,
            notify_generic_error=notify_generic_error,
        ),
        fatal_error_types=(ValueError,),
    )

    async def execute() -> None:
        raise ValueError("fatal")

    with pytest.raises(ValueError, match="fatal"):
        await controller.run_execution(session, execute)

    assert session.turn_status == "failed"
    assert closed == ["session-1"]
    assert store.updated[-1]["status"] == "failed"
    assert store.updated[-1]["error"] == "fatal"


@pytest.mark.asyncio
async def test_runtime_turn_controller_run_execution_can_start_run_before_failure() -> (
    None
):
    store = RecordingRuntimeStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    turn_run = RuntimeTurnRunTracker(
        lifecycle=lifecycle,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeTurnSession(id="session-1", tape_id="tape-1")

    class SubmitFailed(RuntimeError):
        pass

    async def close_runtime(session: FakeTurnSession) -> None:
        del session

    async def notify_generic_error(
        session: FakeTurnSession,
        exc: Exception,
    ) -> None:
        del session, exc

    controller = RuntimeTurnController(
        error_handler=RuntimeTurnErrorHandler(
            turn_run=turn_run,
            close_runtime=close_runtime,
            notify_generic_error=notify_generic_error,
        ),
    )

    async def execute() -> None:
        raise SubmitFailed("submit failed")

    await controller.run_execution(
        session,
        execute,
        ensure_started_error_types=(SubmitFailed,),
    )

    assert turn_run.created is True
    assert store.created[0].status == "queued"
    assert [update["status"] for update in store.updated] == ["running", "failed"]
    assert store.updated[-1]["error"] == "submit failed"


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
async def test_runtime_turn_error_handler_records_generic_failure_with_empty_message() -> (
    None
):
    """An exception with empty str() must still persist a non-empty error."""
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

    await tracker.ensure_started(session)
    handler = RuntimeTurnErrorHandler(
        turn_run=tracker,
        close_runtime=close_runtime,
        notify_generic_error=notify_generic_error,
    )

    await handler.handle_generic(session, RuntimeError())

    assert session.turn_status == "failed"
    assert store.updated[-1]["error"] == "RuntimeError"
    assert session.last_failure_details == "HTTP session turn failed: RuntimeError"


@pytest.mark.asyncio
async def test_turn_failure_with_empty_exception_message_finishes_run(
    tmp_path,
) -> None:
    """Reproduce the original incident end to end at run level.

    A provider exception whose str() is empty must finish the run with a
    non-empty, informative error; the runtime store must not raise
    ValueError("error must be non-empty") and mask the failure.
    """
    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite3")
    persistence = RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=None,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
        },
    )
    session = FakeTurnSession(id="session-1", tape_id=None)
    await persistence.lifecycle().start(
        session,
        run_id="run-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    persisted: list[FakeTurnSession] = []

    async def persist_session(session: FakeTurnSession) -> None:
        persisted.append(session)

    finalizer = persistence.turn_finalizer(persist_session=persist_session)
    outcome = TurnOutcome(
        stop_reason=StopReason.ERROR,
        error=exception_error_message(RuntimeError()),
    )

    await finalizer.complete(
        session,
        ctx=FakeRuntimeContext("tape-1"),
        outcome=outcome,
        run_id="run-1",
    )

    record = await store.load_agent_run("run-1")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "RuntimeError"
    assert session.last_failure_details == "Agent turn failed: RuntimeError"
    assert persisted == [session]


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
