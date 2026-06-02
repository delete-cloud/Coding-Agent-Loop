from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.stores import RuntimeRunLifecycleStore

RuntimeRunStore = RuntimeRunLifecycleStore


class RuntimeRunSession(Protocol):
    id: str
    tape_id: str | None


class RuntimeRunResumeContext(Protocol):
    previous_run_id: str


class RuntimeTurnSession(Protocol):
    tape_id: str | None
    turn_status: str
    last_failure_details: str | None


class RuntimeTurnStartSession(RuntimeRunSession, Protocol):
    runtime_message_bus: object


class RuntimeTurnBinding(Protocol):
    ctx: Any
    adapter: object


class RuntimeMessageSnapshotSaver(Protocol):
    async def __call__(
        self,
        session: RuntimeTurnSession,
        ctx: Any,
        *,
        run_id: str,
    ) -> None: ...


class RuntimeRunFinisher(Protocol):
    async def __call__(
        self,
        session: RuntimeTurnSession,
        *,
        run_id: str,
        status: str,
        result: JSONObject,
        error: str | None,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> None: ...


class RuntimeSessionPersister(Protocol):
    async def __call__(self, session: RuntimeTurnSession) -> None: ...


class RuntimeObservationCompleter(Protocol):
    def __call__(self, *, ctx: Any, turn_status: str) -> None: ...


class RuntimeTurnErrorAction(Protocol):
    async def __call__(self) -> None: ...


class RuntimeRootRunIdentityBinder(Protocol):
    def __call__(
        self,
        session: RuntimeTurnStartSession,
        ctx: Any,
        run_id: str,
        *,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> None: ...


class RuntimeSubagentMessagePublisherBinder(Protocol):
    def __call__(self, ctx: Any) -> None: ...


class RuntimeObservationStarter(Protocol):
    def __call__(
        self,
        *,
        session: RuntimeTurnStartSession,
        ctx: Any,
        run_id: str,
        prompt: str,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> object | None: ...


class RuntimeRunMetadataProvider(Protocol):
    def __call__(
        self,
        session: RuntimeRunSession,
        *,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> JSONObject: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def runtime_result_from_turn_outcome(outcome: TurnOutcome) -> JSONObject:
    return {
        "stop_reason": outcome.stop_reason.value,
        "steps_taken": outcome.steps_taken,
    }


def runtime_status_from_turn_outcome(outcome: TurnOutcome) -> str:
    if outcome.stop_reason == StopReason.INTERRUPTED:
        return "interrupted"
    if outcome.error is not None or outcome.stop_reason == StopReason.ERROR:
        return "failed"
    return "completed"


def require_runtime_turn_outcome(outcome: object) -> TurnOutcome:
    if not isinstance(outcome, TurnOutcome):
        raise TypeError("runtime store requires PipelineAdapter.run_turn outcome")
    return outcome


def _set_failure_details_from_outcome(
    session: RuntimeTurnSession,
    *,
    turn_status: str,
    outcome: TurnOutcome,
) -> None:
    if turn_status == "failed":
        session.turn_status = "failed"
        reason = outcome.error or outcome.stop_reason.value
        session.last_failure_details = f"Agent turn failed: {reason}"
        return
    session.last_failure_details = None


@dataclass(frozen=True)
class RuntimeRunLifecycle:
    store: RuntimeRunLifecycleStore | None
    metadata_for_session: RuntimeRunMetadataProvider
    now: Callable[[], datetime] = _utc_now

    async def create(
        self,
        session: RuntimeRunSession,
        *,
        run_id: str,
        started_at: datetime,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> bool:
        if self.store is None:
            return False
        await self.store.create_agent_run(
            AgentRunRecord(
                run_id=run_id,
                session_id=session.id,
                tape_id=session.tape_id,
                parent_run_id=(
                    None if resume_context is None else resume_context.previous_run_id
                ),
                agent_id=None,
                status="queued",
                started_at=started_at,
                metadata=self.metadata_for_session(
                    session,
                    resume_context=resume_context,
                ),
                result={},
                error=None,
            )
        )
        return True

    async def start(
        self,
        session: RuntimeRunSession,
        *,
        run_id: str,
        started_at: datetime,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> bool:
        created = await self.create(
            session,
            run_id=run_id,
            started_at=started_at,
            resume_context=resume_context,
        )
        if not created:
            return False
        await self.update(
            session,
            run_id=run_id,
            status="running",
            ended_at=None,
            result={},
            error=None,
            resume_context=resume_context,
        )
        return True

    async def update(
        self,
        session: RuntimeRunSession,
        *,
        run_id: str,
        status: str,
        ended_at: datetime | None,
        result: JSONObject,
        error: str | None,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> None:
        if self.store is None:
            return
        await self.store.update_agent_run(
            run_id,
            status=status,
            ended_at=ended_at,
            metadata=self.metadata_for_session(
                session,
                resume_context=resume_context,
            ),
            result=result,
            error=error,
        )

    async def finish(
        self,
        session: RuntimeRunSession,
        *,
        run_id: str,
        status: str,
        result: JSONObject,
        error: str | None,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> None:
        await self.update(
            session,
            run_id=run_id,
            status=status,
            ended_at=self.now(),
            result=result,
            error=error,
            resume_context=resume_context,
        )


@dataclass
class RuntimeTurnRunTracker:
    lifecycle: RuntimeRunLifecycle
    run_id: str
    started_at: datetime
    resume_context: RuntimeRunResumeContext | None = None
    created: bool = False

    async def ensure_started(self, session: RuntimeRunSession) -> None:
        if self.created:
            return
        self.created = await self.lifecycle.start(
            session,
            run_id=self.run_id,
            started_at=self.started_at,
            resume_context=self.resume_context,
        )

    async def finish_if_started(
        self,
        session: RuntimeRunSession,
        *,
        status: str,
        result: JSONObject,
        error: str | None,
    ) -> None:
        if not self.created:
            return
        await self.lifecycle.finish(
            session,
            run_id=self.run_id,
            status=status,
            result=result,
            error=error,
            resume_context=self.resume_context,
        )


@dataclass
class RuntimeTurnErrorState:
    handled: bool = False
    handler_failed: bool = False

    async def handle(self, action: RuntimeTurnErrorAction) -> None:
        try:
            await action()
        except BaseException:
            self.handler_failed = True
            raise
        self.handled = True


@dataclass(frozen=True)
class RuntimeTurnStarter:
    turn_run: RuntimeTurnRunTracker
    consumer: object
    run_id: str
    prompt: str
    bind_root_run_identity: RuntimeRootRunIdentityBinder
    bind_subagent_message_publisher: RuntimeSubagentMessagePublisherBinder
    start_observation: RuntimeObservationStarter
    resume_context: RuntimeRunResumeContext | None = None

    async def start(
        self,
        session: RuntimeTurnStartSession,
        binding: RuntimeTurnBinding,
    ) -> object | None:
        ctx = binding.ctx
        adapter = binding.adapter
        self.bind_root_run_identity(
            session,
            ctx,
            self.run_id,
            resume_context=self.resume_context,
        )
        await self.turn_run.ensure_started(session)
        set_consumer = getattr(adapter, "set_consumer", None)
        if callable(set_consumer):
            set_consumer(self.consumer)
        ctx.runtime_message_bus = session.runtime_message_bus
        ctx.config["wire_consumer"] = self.consumer
        self.bind_subagent_message_publisher(ctx)
        return self.start_observation(
            session=session,
            ctx=ctx,
            run_id=self.run_id,
            prompt=self.prompt,
            resume_context=self.resume_context,
        )


@dataclass(frozen=True)
class RuntimeTurnFinalizer:
    has_runtime_store: bool
    save_message_snapshot: RuntimeMessageSnapshotSaver
    finish_run: RuntimeRunFinisher
    persist_session: RuntimeSessionPersister
    complete_observation: RuntimeObservationCompleter | None = None

    async def complete(
        self,
        session: RuntimeTurnSession,
        *,
        ctx: Any,
        outcome: object,
        run_id: str,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> None:
        session.tape_id = ctx.tape.tape_id
        if self.has_runtime_store:
            turn_outcome = require_runtime_turn_outcome(outcome)
            turn_status = runtime_status_from_turn_outcome(turn_outcome)
            await self.save_message_snapshot(session, ctx, run_id=run_id)
            await self.finish_run(
                session,
                run_id=run_id,
                status=turn_status,
                result=runtime_result_from_turn_outcome(turn_outcome),
                error=turn_outcome.error,
                resume_context=resume_context,
            )
            _set_failure_details_from_outcome(
                session,
                turn_status=turn_status,
                outcome=turn_outcome,
            )
        else:
            turn_status = "completed"
            if isinstance(outcome, TurnOutcome):
                turn_status = runtime_status_from_turn_outcome(outcome)
                _set_failure_details_from_outcome(
                    session,
                    turn_status=turn_status,
                    outcome=outcome,
                )
            else:
                session.last_failure_details = None
        if self.complete_observation is not None:
            self.complete_observation(ctx=ctx, turn_status=turn_status)
        await self.persist_session(session)


__all__ = [
    "RuntimeMessageSnapshotSaver",
    "RuntimeObservationCompleter",
    "RuntimeRunLifecycle",
    "RuntimeRunLifecycleStore",
    "RuntimeRunFinisher",
    "RuntimeRootRunIdentityBinder",
    "RuntimeRunMetadataProvider",
    "RuntimeRunResumeContext",
    "RuntimeRunSession",
    "RuntimeRunStore",
    "RuntimeSessionPersister",
    "RuntimeObservationStarter",
    "RuntimeSubagentMessagePublisherBinder",
    "RuntimeTurnBinding",
    "RuntimeTurnErrorAction",
    "RuntimeTurnErrorState",
    "RuntimeTurnFinalizer",
    "RuntimeTurnRunTracker",
    "RuntimeTurnStartSession",
    "RuntimeTurnStarter",
    "RuntimeTurnSession",
    "require_runtime_turn_outcome",
    "runtime_result_from_turn_outcome",
    "runtime_status_from_turn_outcome",
]
