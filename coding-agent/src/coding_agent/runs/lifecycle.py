from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any, Protocol, cast

from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.stores import RuntimeRunLifecycleStore

RuntimeRunStore = RuntimeRunLifecycleStore


class RuntimeRunSession(Protocol):
    id: str
    tape_id: str | None


class RuntimeRunResumeContext(Protocol):
    previous_run_id: str

    def metadata(self) -> JSONObject: ...


class RuntimeTurnSession(Protocol):
    tape_id: str | None
    turn_status: str
    last_failure_details: str | None


class RuntimeTurnStateSession(RuntimeTurnSession, Protocol):
    last_activity: datetime
    turn_in_progress: bool
    current_turn_id: str | None
    task: object | None


class RuntimeTurnErrorSession(RuntimeTurnSession, RuntimeRunSession, Protocol):
    pass


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


class RuntimeTurnStatePersister(Protocol):
    async def __call__(self, session: RuntimeTurnStateSession) -> None: ...


class RuntimeObservationCompleter(Protocol):
    def __call__(self, *, ctx: Any, turn_status: str) -> None: ...


class RuntimeTurnObservationRecorder(Protocol):
    def fail_turn(self, *, error_type: str) -> None: ...

    def cancel_turn(self) -> None: ...


class RuntimeTurnObservationCompleter(Protocol):
    def __call__(
        self,
        recorder: RuntimeTurnObservationRecorder | None,
        *,
        ctx: Any,
        turn_status: str,
    ) -> None: ...


class RuntimeTurnErrorAction(Protocol):
    async def __call__(self) -> None: ...


class RuntimeTurnExecution(Protocol):
    def __call__(self) -> Awaitable[None]: ...


class RuntimeSessionCloser(Protocol):
    async def __call__(self, session: RuntimeTurnErrorSession) -> None: ...


class RuntimeHandleSession(Protocol):
    def detach_runtime_adapter(self) -> object | None: ...


class RuntimeGenericErrorNotifier(Protocol):
    async def __call__(
        self,
        session: RuntimeTurnErrorSession,
        exc: Exception,
    ) -> None: ...


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
class RuntimeCloser:
    async def close(self, session: RuntimeHandleSession) -> None:
        adapter = session.detach_runtime_adapter()
        await self.close_adapter(adapter)

    async def close_adapter(self, adapter: object | None) -> None:
        if adapter is None:
            return
        close = getattr(adapter, "close", None)
        if callable(close):
            close_result = close()
            if isawaitable(close_result):
                await close_result

    def close_sync_safe(self, session: RuntimeHandleSession) -> None:
        adapter = session.detach_runtime_adapter()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.close_adapter(adapter))
            return
        _ = loop.create_task(self.close_adapter(adapter))


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


@dataclass
class RuntimeTurnObservationState:
    complete_observation: RuntimeTurnObservationCompleter | None = None
    recorder: RuntimeTurnObservationRecorder | None = None

    def set(self, recorder: object | None) -> None:
        self.recorder = cast(RuntimeTurnObservationRecorder | None, recorder)

    def complete(self, *, ctx: Any, turn_status: str) -> None:
        if self.complete_observation is None:
            return
        self.complete_observation(
            self.recorder,
            ctx=ctx,
            turn_status=turn_status,
        )

    def fail(self, error_type: str) -> None:
        if self.recorder is None:
            return
        self.recorder.fail_turn(error_type=error_type)

    def cancel(self) -> None:
        if self.recorder is None:
            return
        self.recorder.cancel_turn()


@dataclass(frozen=True)
class RuntimeTurnSessionState:
    persist_session: RuntimeTurnStatePersister
    now: Callable[[], datetime] = _utc_now

    async def begin(
        self,
        session: RuntimeTurnStateSession,
        *,
        run_id: str,
    ) -> datetime:
        session.last_activity = self.now()
        session.turn_in_progress = True
        session.turn_status = "running"
        started_at = self.now()
        session.current_turn_id = run_id
        session.last_failure_details = None
        await self.persist_session(session)
        return started_at

    async def finalize(
        self,
        session: RuntimeTurnStateSession,
        *,
        current_task: object | None,
    ) -> None:
        if session.task is None or session.task is not current_task:
            session.turn_in_progress = False
            if session.turn_status == "running":
                session.turn_status = "idle"
        session.last_activity = self.now()
        await self.persist_session(session)


@dataclass(frozen=True)
class RuntimeTurnErrorHandler:
    turn_run: RuntimeTurnRunTracker
    close_runtime: RuntimeSessionCloser
    notify_generic_error: RuntimeGenericErrorNotifier
    fail_observation: Callable[[str], None] | None = None
    cancel_observation: Callable[[], None] | None = None

    async def handle_fatal(
        self,
        session: RuntimeTurnErrorSession,
        exc: BaseException,
    ) -> None:
        if self.fail_observation is not None:
            self.fail_observation(type(exc).__name__)
        await self.turn_run.finish_if_started(
            session,
            status="failed",
            result={},
            error=str(exc),
        )
        session.turn_status = "failed"
        session.last_failure_details = f"Fatal tool execution failed: {exc}"
        await self.close_runtime(session)

    async def handle_cancelled(self, session: RuntimeTurnErrorSession) -> None:
        if self.cancel_observation is not None:
            self.cancel_observation()
        await self.turn_run.finish_if_started(
            session,
            status="cancelled",
            result={},
            error="cancelled",
        )

    async def handle_generic(
        self,
        session: RuntimeTurnErrorSession,
        exc: Exception,
        *,
        ensure_started: bool = False,
    ) -> None:
        if ensure_started:
            await self.turn_run.ensure_started(session)
        if self.fail_observation is not None:
            self.fail_observation(type(exc).__name__)
        await self.turn_run.finish_if_started(
            session,
            status="failed",
            result={},
            error=str(exc),
        )
        session.turn_status = "failed"
        session.last_failure_details = f"HTTP session turn failed: {exc}"
        await self.close_runtime(session)
        await self.notify_generic_error(session, exc)


@dataclass
class RuntimeTurnController:
    error_handler: RuntimeTurnErrorHandler
    starter: RuntimeTurnStarter | None = None
    finalizer: RuntimeTurnFinalizer | None = None
    observation: RuntimeTurnObservationState | None = None
    error_state: RuntimeTurnErrorState = field(default_factory=RuntimeTurnErrorState)
    fatal_error_types: tuple[type[BaseException], ...] = ()
    cancelled_error_types: tuple[type[BaseException], ...] = ()

    async def before_turn(
        self,
        session: RuntimeTurnStartSession,
        binding: RuntimeTurnBinding,
    ) -> None:
        if self.starter is None:
            raise RuntimeError("RuntimeTurnController requires starter for before_turn")
        recorder = await self.starter.start(session, binding)
        if self.observation is not None:
            self.observation.set(recorder)

    async def after_turn(
        self,
        session: RuntimeTurnSession,
        binding: RuntimeTurnBinding,
        outcome: object,
    ) -> None:
        if self.finalizer is None:
            raise RuntimeError(
                "RuntimeTurnController requires finalizer for after_turn"
            )
        await self.finalizer.complete(
            session,
            ctx=binding.ctx,
            outcome=outcome,
            run_id=self._require_run_id(),
            resume_context=None
            if self.starter is None
            else self.starter.resume_context,
        )

    async def on_turn_error(
        self,
        session: RuntimeTurnErrorSession,
        exc: BaseException,
    ) -> None:
        if self._is_fatal(exc):
            await self.error_state.handle(
                lambda: self.error_handler.handle_fatal(session, exc)
            )
            return
        if self._is_cancelled(exc):
            await self.error_state.handle(
                lambda: self.error_handler.handle_cancelled(session)
            )
            return
        if isinstance(exc, Exception):
            await self.error_state.handle(
                lambda: self.error_handler.handle_generic(session, exc)
            )

    async def handle_outer_exception(
        self,
        session: RuntimeTurnErrorSession,
        exc: BaseException,
        *,
        ensure_started: bool = False,
    ) -> bool:
        if self.error_state.handler_failed:
            return True
        if self.error_state.handled:
            return self._is_fatal(exc) or self._is_cancelled(exc)
        if self._is_fatal(exc):
            await self.error_handler.handle_fatal(session, exc)
            return True
        if self._is_cancelled(exc):
            await self.error_handler.handle_cancelled(session)
            return True
        if isinstance(exc, Exception):
            await self.error_handler.handle_generic(
                session,
                exc,
                ensure_started=ensure_started,
            )
            return False
        return True

    async def run_execution(
        self,
        session: RuntimeTurnErrorSession,
        execute: RuntimeTurnExecution,
        *,
        ensure_started_error_types: tuple[type[BaseException], ...] = (),
    ) -> None:
        try:
            await execute()
        except BaseException as exc:
            ensure_started = bool(ensure_started_error_types) and isinstance(
                exc,
                ensure_started_error_types,
            )
            if await self.handle_outer_exception(
                session,
                exc,
                ensure_started=ensure_started,
            ):
                raise

    def _is_fatal(self, exc: BaseException) -> bool:
        return bool(self.fatal_error_types) and isinstance(exc, self.fatal_error_types)

    def _is_cancelled(self, exc: BaseException) -> bool:
        return bool(self.cancelled_error_types) and isinstance(
            exc,
            self.cancelled_error_types,
        )

    def _require_run_id(self) -> str:
        if self.starter is None:
            raise RuntimeError("RuntimeTurnController requires starter for after_turn")
        return self.starter.run_id


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
    "RuntimeTurnController",
    "RuntimeTurnErrorAction",
    "RuntimeTurnErrorHandler",
    "RuntimeTurnErrorSession",
    "RuntimeTurnErrorState",
    "RuntimeTurnFinalizer",
    "RuntimeTurnObservationCompleter",
    "RuntimeTurnObservationRecorder",
    "RuntimeTurnObservationState",
    "RuntimeTurnRunTracker",
    "RuntimeTurnSessionState",
    "RuntimeTurnStatePersister",
    "RuntimeTurnStateSession",
    "RuntimeTurnStartSession",
    "RuntimeTurnStarter",
    "RuntimeTurnSession",
    "require_runtime_turn_outcome",
    "runtime_result_from_turn_outcome",
    "runtime_status_from_turn_outcome",
]
