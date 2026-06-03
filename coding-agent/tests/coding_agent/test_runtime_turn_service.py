from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from agentkit.tools import FatalToolExecutionError

from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.executors.local_daemon import (
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimeExecution,
)
from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.runs import (
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RuntimeControlServices,
    RunCoordinatorError,
    RunRequest,
    RunTarget,
)
from coding_agent.runs.persistence import RuntimeRunPersistenceService
from coding_agent.runs.turn_execution import RuntimeTurnService
from coding_agent.runs.turn_service_factory import RuntimeTurnServiceFactory
from coding_agent.wire.protocol import WireMessage


@dataclass
class FakeSession:
    id: str = "session-1"
    default_run_target: RunTarget | None = None
    tape_id: str | None = "tape-1"
    turn_status: str = "idle"
    last_failure_details: str | None = None
    last_activity: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    turn_in_progress: bool = False
    current_turn_id: str | None = None
    task: object | None = None
    runtime_message_bus: object | None = None

    def __post_init__(self) -> None:
        if self.default_run_target is None:
            self.default_run_target = _target()


class FakeRuntimeContext:
    def __init__(self) -> None:
        self.tape = type("Tape", (), {"tape_id": "tape-1"})()
        self.config: dict[str, object] = {}
        self.runtime_message_bus: object | None = None


class FakeAdapter:
    def __init__(self) -> None:
        self.consumer: object | None = None

    def set_consumer(self, consumer: object) -> None:
        self.consumer = consumer


class FakeObservationRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def fail_turn(self, *, error_type: str) -> None:
        self.events.append(("fail", error_type))

    def cancel_turn(self) -> None:
        self.events.append(("cancel", ""))


@dataclass(frozen=True)
class FakeResumeContext:
    previous_run_id: str


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


class RecordingCoordinator:
    def __init__(
        self,
        *,
        fail_on_submit: BaseException | None = None,
        fail_before_prepare: BaseException | None = None,
        fail_after_prepare: BaseException | None = None,
    ) -> None:
        self.requests: list[RunRequest] = []
        self.executions: list[LocalDaemonRuntimeExecution] = []
        self.fail_on_submit = fail_on_submit
        self.fail_before_prepare = fail_before_prepare
        self.fail_after_prepare = fail_after_prepare

    async def submit_run(self, request: RunRequest) -> object:
        self.requests.append(request)
        if self.fail_on_submit is not None:
            raise self.fail_on_submit
        return object()

    async def execute_runtime(self, execution: LocalDaemonRuntimeExecution) -> object:
        self.executions.append(execution)
        if self.fail_before_prepare is not None:
            raise self.fail_before_prepare
        binding = await execution.runtime_provider.prepare_runtime(execution.request)
        if execution.before_turn is not None:
            await execution.before_turn(binding)
        if self.fail_after_prepare is not None:
            if execution.on_turn_error is not None:
                await execution.on_turn_error(binding, self.fail_after_prepare)
            raise self.fail_after_prepare
        outcome = TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS, steps_taken=2)
        if execution.after_turn is not None:
            await execution.after_turn(binding, outcome)
        return outcome


def _target() -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path="/repo"),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


def _persistence(store: RecordingRuntimeStore) -> RuntimeRunPersistenceService:
    return RuntimeRunPersistenceService(
        run_store=store,
        checkpoint_store=None,
        metadata_for_session=lambda session, *, resume_context=None: {
            "session_id": session.id,
            "resume_from": None
            if resume_context is None
            else resume_context.previous_run_id,
        },
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


async def _list_session_ids() -> list[str]:
    return ["session-1"]


async def _recoverable(session_id: str) -> bool:
    return session_id == "session-1"


def _service(
    *,
    store: RecordingRuntimeStore,
    coordinator: RecordingCoordinator,
    emitted: list[WireMessage] | None = None,
    closed: list[str] | None = None,
) -> RuntimeTurnService:
    async def persist_session(session: FakeSession) -> None:
        del session

    async def prepare_runtime(
        session: FakeSession,
        *,
        consumer: object,
        request: RunRequest,
    ) -> LocalDaemonRuntimeBinding:
        del session, request
        ctx = FakeRuntimeContext()
        adapter = FakeAdapter()
        return LocalDaemonRuntimeBinding(
            pipeline=object(),
            ctx=ctx,
            adapter=adapter,
        )

    async def close_runtime(session: FakeSession) -> None:
        if closed is not None:
            closed.append(session.id)

    async def emit_message(session: FakeSession, message: WireMessage) -> None:
        del session
        if emitted is not None:
            emitted.append(message)

    return RuntimeTurnService(
        run_coordinator=coordinator,
        runtime_run_persistence=_persistence(store),
        persist_session=persist_session,
        make_consumer=lambda session: f"consumer:{session.id}",
        prepare_runtime=prepare_runtime,
        close_runtime=close_runtime,
        emit_message=emit_message,
        bind_root_run_identity=lambda session, ctx, run_id, *, resume_context=None: (
            setattr(ctx, "root_run_id", run_id)
        ),
        bind_subagent_message_publisher=lambda ctx: setattr(
            ctx,
            "subagent_publisher_bound",
            True,
        ),
        start_observation=lambda **kwargs: FakeObservationRecorder(),
        complete_observation=lambda recorder, *, ctx, turn_status: setattr(
            ctx,
            "observation_completed",
            (recorder, turn_status),
        ),
        log_turn_exception=lambda message: None,
        fatal_error_types=(ValueError,),
        cancelled_error_types=(),
    )


def test_runtime_turn_service_factory_builds_service_with_latest_runtime_store() -> (
    None
):
    store_a = RecordingRuntimeStore()
    store_b = RecordingRuntimeStore()
    current_store: RecordingRuntimeStore = store_a
    coordinator_a = RecordingCoordinator()
    coordinator_b = RecordingCoordinator()

    async def persist_session(session: FakeSession) -> None:
        del session

    async def prepare_runtime(
        session: FakeSession,
        *,
        consumer: object,
        request: RunRequest,
    ) -> LocalDaemonRuntimeBinding:
        del session, consumer, request
        return LocalDaemonRuntimeBinding(
            pipeline=object(),
            ctx=FakeRuntimeContext(),
            adapter=FakeAdapter(),
        )

    async def close_runtime(session: FakeSession) -> None:
        del session

    async def emit_message(session: FakeSession, message: WireMessage) -> None:
        del session, message

    factory = RuntimeTurnServiceFactory(
        runtime_control_services=RuntimeControlServices(
            store=lambda: current_store,
            metadata_for_session=lambda session, *, run_id=None, resume_context=None: {
                "session_id": session.id,
                "run_id": run_id,
                "resume_context": resume_context,
            },
            list_session_ids=_list_session_ids,
            session_is_recoverable=_recoverable,
            owner_id=lambda: "owner-1",
            active_resume_blocking_statuses=frozenset({"running"}),
        ),
        persist_session=persist_session,
        make_consumer=lambda session: f"consumer:{session.id}",
        prepare_runtime=prepare_runtime,
        close_runtime=close_runtime,
        emit_message=emit_message,
        bind_root_run_identity=lambda session, ctx, run_id, *, resume_context=None: (
            setattr(ctx, "root_run_id", run_id)
        ),
        bind_subagent_message_publisher=lambda ctx: setattr(
            ctx,
            "subagent_publisher_bound",
            True,
        ),
        start_observation=lambda **kwargs: FakeObservationRecorder(),
        complete_observation=lambda recorder, *, ctx, turn_status: setattr(
            ctx,
            "observation_completed",
            (recorder, turn_status),
        ),
    )

    service_a = factory.build(coordinator_a)
    current_store = store_b
    service_b = factory.build(coordinator_b)

    assert service_a.run_coordinator is coordinator_a
    assert service_a.runtime_run_persistence.run_store is store_a
    assert service_a.runtime_run_persistence.checkpoint_store is store_a
    assert service_b.run_coordinator is coordinator_b
    assert service_b.runtime_run_persistence.run_store is store_b
    assert service_b.runtime_run_persistence.checkpoint_store is store_b
    assert service_b.fatal_error_types == (FatalToolExecutionError,)
    assert service_b.cancelled_error_types == (asyncio.CancelledError,)


@pytest.mark.asyncio
async def test_runtime_turn_service_executes_coordinator_runtime_path() -> None:
    store = RecordingRuntimeStore()
    coordinator = RecordingCoordinator()
    session = FakeSession(runtime_message_bus=object())

    await _service(store=store, coordinator=coordinator).run(
        session,
        prompt="hello",
        run_id="run-1",
        current_task=None,
    )

    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.current_turn_id == "run-1"
    assert len(coordinator.requests) == 1
    assert len(coordinator.executions) == 1
    execution = coordinator.executions[0]
    assert execution.request == coordinator.requests[0]
    assert execution.prompt == "hello"
    assert execution.request.input_summary == "hello"
    assert store.created[0].status == "queued"
    assert [update["status"] for update in store.updated] == [
        "running",
        "completed",
    ]


@pytest.mark.asyncio
async def test_runtime_turn_service_builds_run_request_from_session_placement() -> None:
    store = RecordingRuntimeStore()
    coordinator = RecordingCoordinator()
    target = RunTarget(
        workspace=LocalPathWorkspaceRef(path="/custom-repo"),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )
    session = FakeSession(default_run_target=target)

    await _service(store=store, coordinator=coordinator).run(
        session,
        prompt="  ",
        run_id="run-2",
        resume_context=FakeResumeContext(previous_run_id="run-1"),
        current_task=None,
    )

    assert len(coordinator.requests) == 1
    request = coordinator.requests[0]
    assert request.session_id == "session-1"
    assert request.run_id == "run-2"
    assert request.target is target
    assert request.input_summary is None
    assert request.resume_from_run_id == "run-1"


@pytest.mark.asyncio
async def test_runtime_turn_service_records_submit_failure_after_starting_run() -> None:
    store = RecordingRuntimeStore()
    emitted: list[WireMessage] = []
    closed: list[str] = []
    coordinator = RecordingCoordinator(
        fail_on_submit=RunCoordinatorError("no executor")
    )
    session = FakeSession()

    await _service(
        store=store,
        coordinator=coordinator,
        emitted=emitted,
        closed=closed,
    ).run(
        session,
        prompt="hello",
        run_id="run-1",
        current_task=None,
    )

    assert session.turn_in_progress is False
    assert session.turn_status == "failed"
    assert len(coordinator.requests) == 1
    assert coordinator.executions == []
    assert closed == ["session-1"]
    assert [update["status"] for update in store.updated] == ["running", "failed"]
    assert store.updated[-1]["error"] == "no executor"
    assert [type(message).__name__ for message in emitted] == [
        "StreamDelta",
        "TurnEnd",
    ]


@pytest.mark.asyncio
async def test_runtime_turn_service_reraises_fatal_runtime_error() -> None:
    store = RecordingRuntimeStore()
    closed: list[str] = []
    coordinator = RecordingCoordinator(fail_after_prepare=ValueError("fatal"))
    session = FakeSession()

    with pytest.raises(ValueError, match="fatal"):
        await _service(
            store=store,
            coordinator=coordinator,
            closed=closed,
        ).run(
            session,
            prompt="hello",
            run_id="run-1",
            current_task=None,
        )

    assert session.turn_in_progress is False
    assert session.turn_status == "failed"
    assert closed == ["session-1"]
    assert store.updated[-1]["status"] == "failed"
    assert store.updated[-1]["error"] == "fatal"
