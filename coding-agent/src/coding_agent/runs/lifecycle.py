from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from inspect import isawaitable
from types import TracebackType
from typing import Any, Protocol, cast

from coding_agent.adapter.types import StopReason, TurnOutcome, exception_error_message
from coding_agent.stores.runtime_store import AgentRunRecord, JSONObject
from coding_agent.stores import RuntimeRunLifecycleStore
from coding_agent.topics.context_pack import merged_context_pack_stash

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
        extra_metadata: JSONObject | None = None,
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


class RuntimeTask(Protocol):
    def cancel(self) -> None: ...

    def done(self) -> bool: ...

    def __await__(self) -> Any: ...


class RuntimeTurnAdmissionLock(Protocol):
    def locked(self) -> bool: ...

    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class RuntimeTurnAdmissionSession(Protocol):
    turn_in_progress: bool
    task: RuntimeTask | None


RuntimeTurnLockProvider = Callable[[str], RuntimeTurnAdmissionLock]
RuntimeWorkspaceExportGuard = Callable[[str], bool]
RuntimeOwnerAsserter = Callable[[str], Awaitable[None]]
RuntimeTurnAdmissionSessionLoader = Callable[
    [str],
    Awaitable[RuntimeTurnAdmissionSession],
]
RuntimeTurnAdmissionBody = Callable[
    [RuntimeTurnAdmissionSession],
    Awaitable[Any],
]
RuntimeMaintenanceAdmissionSession = RuntimeTurnAdmissionSession
RuntimeMaintenanceAdmissionBody = Callable[
    [RuntimeMaintenanceAdmissionSession],
    Awaitable[Any],
]


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


_EMPTY_ASSISTANT_RESPONSE_ERROR = "Agent completed without an assistant response"


def _is_empty_completed_response(outcome: TurnOutcome) -> bool:
    return (
        outcome.error is None
        and outcome.stop_reason != StopReason.ERROR
        and outcome.stop_reason != StopReason.INTERRUPTED
        and outcome.steps_taken == 0
        and (outcome.final_message is None or not outcome.final_message.strip())
    )


def _runtime_error_from_turn_outcome(outcome: TurnOutcome) -> str | None:
    if _is_empty_completed_response(outcome):
        return _EMPTY_ASSISTANT_RESPONSE_ERROR
    return outcome.error


def runtime_result_from_turn_outcome(outcome: TurnOutcome) -> JSONObject:
    return {
        "stop_reason": outcome.stop_reason.value,
        "steps_taken": outcome.steps_taken,
        "text": outcome.final_message,
    }


def runtime_status_from_turn_outcome(outcome: TurnOutcome) -> str:
    if outcome.stop_reason == StopReason.INTERRUPTED:
        return "interrupted"
    if (
        outcome.error is not None
        or outcome.stop_reason == StopReason.ERROR
        or _is_empty_completed_response(outcome)
    ):
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
        reason = _runtime_error_from_turn_outcome(outcome) or outcome.stop_reason.value
        session.last_failure_details = f"Agent turn failed: {reason}"
        return
    session.last_failure_details = None


def _task_finished(task: object | None) -> bool:
    if task is None:
        return False
    done = getattr(task, "done", None)
    if callable(done) and done():
        return True
    cancelled = getattr(task, "cancelled", None)
    return bool(callable(cancelled) and cancelled())


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
class RuntimeTaskStopper:
    timeout: float = 5.0

    async def stop(
        self,
        *,
        session_id: str,
        task: RuntimeTask | None,
    ) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=self.timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        if not task.done():
            raise RuntimeError(
                f"Session task for {session_id} did not stop after cancellation"
            )


@dataclass(frozen=True)
class RuntimeTurnAdmissionService:
    turn_lock_for: RuntimeTurnLockProvider
    workspace_export_in_progress: RuntimeWorkspaceExportGuard
    assert_owner: RuntimeOwnerAsserter
    load_session: RuntimeTurnAdmissionSessionLoader

    async def prepare_session_turn(
        self,
        session_id: str,
    ) -> RuntimeTurnAdmissionSession:
        lock = self.turn_lock_for(session_id)
        if lock.locked():
            raise RuntimeError("turn already in progress")
        if self.workspace_export_in_progress(session_id):
            raise RuntimeError("turn already in progress")

        session = await self.load_session(session_id)
        await self.assert_owner(session_id)
        if session.turn_in_progress or (
            session.task is not None and not session.task.done()
        ):
            raise RuntimeError("turn already in progress")
        return session

    async def run_exclusive(
        self,
        session_id: str,
        body: RuntimeTurnAdmissionBody,
    ) -> Any:
        lock = self.turn_lock_for(session_id)
        if lock.locked():
            raise RuntimeError("turn already in progress")

        async with lock:
            if self.workspace_export_in_progress(session_id):
                raise RuntimeError("turn already in progress")
            await self.assert_owner(session_id)
            session = await self.load_session(session_id)
            return await body(session)


@dataclass(frozen=True)
class RuntimeMaintenanceAdmissionService:
    turn_lock_for: RuntimeTurnLockProvider
    assert_owner: RuntimeOwnerAsserter
    load_session: RuntimeTurnAdmissionSessionLoader

    async def run_exclusive(
        self,
        session_id: str,
        body: RuntimeMaintenanceAdmissionBody,
    ) -> Any:
        lock = self.turn_lock_for(session_id)
        if lock.locked():
            raise RuntimeError("turn already in progress")

        async with lock:
            await self.assert_owner(session_id)
            session = await self.load_session(session_id)
            if session.turn_in_progress or (
                session.task is not None and not session.task.done()
            ):
                raise RuntimeError("turn already in progress")
            return await body(session)


@dataclass(frozen=True)
class RuntimeRunLifecycle:
    store: RuntimeRunLifecycleStore | None
    metadata_for_session: RuntimeRunMetadataProvider
    settle_root_run: Callable[..., Awaitable[object]] | None = None
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
        extra_metadata: JSONObject | None = None,
    ) -> None:
        if self.store is None:
            return
        metadata = self.metadata_for_session(
            session,
            resume_context=resume_context,
        )
        if extra_metadata:
            metadata.update(extra_metadata)
        await self.store.update_agent_run(
            run_id,
            status=status,
            ended_at=ended_at,
            metadata=metadata,
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
        extra_metadata: JSONObject | None = None,
    ) -> None:
        if self.settle_root_run is not None:
            result_text = result.get("text")
            if result_text is not None and not isinstance(result_text, str):
                raise TypeError("root run result text must be a string")
            await self.settle_root_run(
                session.id,
                run_id=run_id,
                outcome=status,
                result=result_text,
                error=error,
                result_payload=result,
                extra_metadata=extra_metadata,
            )
            return
        await self.update(
            session,
            run_id=run_id,
            status=status,
            ended_at=self.now(),
            result=result,
            error=error,
            resume_context=resume_context,
            extra_metadata=extra_metadata,
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
    persist_begin: RuntimeTurnStatePersister | None = None
    persist_finalize: RuntimeTurnStatePersister | None = None

    async def _persist(
        self,
        session: RuntimeTurnStateSession,
        persist: RuntimeTurnStatePersister | None,
    ) -> None:
        writer = persist if persist is not None else self.persist_session
        await writer(session)

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
        await self._persist(session, self.persist_begin)
        return started_at

    async def finalize(
        self,
        session: RuntimeTurnStateSession,
        *,
        current_task: object | None,
        turn_finished: bool = False,
    ) -> None:
        session_task = session.task
        owns_task = session_task is not None and session_task is current_task
        clear_admission = not owns_task or turn_finished or _task_finished(session_task)

        if clear_admission:
            if owns_task:
                session.task = None
            session.turn_in_progress = False
            if session.turn_status == "running":
                session.turn_status = "idle"
        session.last_activity = self.now()
        await self._persist(session, self.persist_finalize)


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
            error=exception_error_message(exc),
        )
        session.turn_status = "failed"
        session.last_failure_details = (
            f"Fatal tool execution failed: {exception_error_message(exc)}"
        )
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
        session.turn_status = "cancelled"

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
            error=exception_error_message(exc),
        )
        session.turn_status = "failed"
        session.last_failure_details = (
            f"HTTP session turn failed: {exception_error_message(exc)}"
        )
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
            turn_error = _runtime_error_from_turn_outcome(turn_outcome)
            await self.save_message_snapshot(session, ctx, run_id=run_id)
            await self.finish_run(
                session,
                run_id=run_id,
                status=turn_status,
                result=runtime_result_from_turn_outcome(turn_outcome),
                error=turn_error,
                resume_context=resume_context,
                extra_metadata=_context_pack_run_metadata(ctx),
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


def _context_pack_run_metadata(ctx: Any) -> JSONObject | None:
    config = getattr(ctx, "config", None)
    if not isinstance(config, Mapping):
        return None
    pack = merged_context_pack_stash(config)
    if pack is None:
        return None
    return {"context_pack": cast(JSONObject, pack)}


__all__ = [
    "RuntimeMaintenanceAdmissionBody",
    "RuntimeMaintenanceAdmissionService",
    "RuntimeMaintenanceAdmissionSession",
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
    "RuntimeTurnAdmissionBody",
    "RuntimeTurnAdmissionLock",
    "RuntimeTurnAdmissionService",
    "RuntimeTurnAdmissionSession",
    "RuntimeTurnAdmissionSessionLoader",
    "RuntimeTurnLockProvider",
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
