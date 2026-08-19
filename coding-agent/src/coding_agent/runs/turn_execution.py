from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from coding_agent.executors.local_daemon import (
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimeExecution,
)
from coding_agent.runs.coordinator import (
    RunCoordinator,
    RunCoordinatorError,
    RunRequest,
)
from coding_agent.runs.target import RunTarget
from coding_agent.runs.lifecycle import (
    RuntimeObservationCompleter,
    RuntimeObservationStarter,
    RuntimeRootRunIdentityBinder,
    RuntimeRunResumeContext,
    RuntimeSessionCloser,
    RuntimeSubagentMessagePublisherBinder,
    RuntimeTurnController,
    RuntimeTurnErrorHandler,
    RuntimeTurnObservationState,
    RuntimeTurnRunTracker,
    RuntimeTurnSessionState,
    RuntimeTurnStatePersister,
    RuntimeTurnStateSession,
    RuntimeTurnStarter,
)
from coding_agent.runs.persistence import RuntimeRunPersistenceService
from coding_agent.wire.runtime import RuntimeTurnWire, RuntimeTurnWireEmitter


class RuntimeTurnConsumerFactory(Protocol):
    def __call__(self, session: RuntimeTurnServiceSession) -> object: ...


class RuntimeProviderPreparer(Protocol):
    async def __call__(
        self,
        session: RuntimeTurnServiceSession,
        *,
        consumer: object,
        request: RunRequest,
    ) -> LocalDaemonRuntimeBinding: ...


class RuntimeCurrentTaskProvider(Protocol):
    def __call__(self) -> object | None: ...


class RuntimeTurnServiceSession(RuntimeTurnStateSession, Protocol):
    id: str
    default_run_target: RunTarget


@dataclass(frozen=True)
class _RuntimeTurnLocalDaemonProvider:
    prepare: Callable[[RunRequest], Awaitable[LocalDaemonRuntimeBinding]]

    async def prepare_runtime(self, request: RunRequest) -> LocalDaemonRuntimeBinding:
        return await self.prepare(request)


@dataclass(frozen=True)
class RuntimeTurnService:
    run_coordinator: RunCoordinator
    runtime_run_persistence: RuntimeRunPersistenceService
    persist_session: RuntimeTurnStatePersister
    make_consumer: RuntimeTurnConsumerFactory
    prepare_runtime: RuntimeProviderPreparer
    close_runtime: RuntimeSessionCloser
    emit_message: RuntimeTurnWireEmitter
    bind_root_run_identity: RuntimeRootRunIdentityBinder
    bind_subagent_message_publisher: RuntimeSubagentMessagePublisherBinder
    start_observation: RuntimeObservationStarter
    persist_turn_started: RuntimeTurnStatePersister | None = None
    persist_turn_settled: RuntimeTurnStatePersister | None = None
    complete_observation: RuntimeObservationCompleter | None = None
    log_turn_exception: Callable[[str], None] | None = None
    fatal_error_types: tuple[type[BaseException], ...] = ()
    cancelled_error_types: tuple[type[BaseException], ...] = ()

    async def run(
        self,
        session: RuntimeTurnServiceSession,
        *,
        prompt: str,
        run_id: str,
        resume_context: RuntimeRunResumeContext | None = None,
        current_task: object | None = None,
    ) -> None:
        turn_session_state = RuntimeTurnSessionState(
            persist_session=self.persist_session,
            persist_begin=self.persist_turn_started,
            persist_finalize=self.persist_turn_settled,
        )
        started_at = await turn_session_state.begin(session, run_id=run_id)
        turn_run = RuntimeTurnRunTracker(
            lifecycle=self.runtime_run_persistence.lifecycle(),
            run_id=run_id,
            started_at=started_at,
            resume_context=resume_context,
        )
        observation = RuntimeTurnObservationState(
            complete_observation=self.complete_observation
        )
        turn_wire = RuntimeTurnWire(
            session_id=session.id,
            run_id=run_id,
            emit_message=self.emit_message,
            log_exception=self.log_turn_exception,
        )
        turn_controller = RuntimeTurnController(
            error_handler=RuntimeTurnErrorHandler(
                turn_run=turn_run,
                close_runtime=self.close_runtime,
                notify_generic_error=turn_wire.notify_generic_error,
                fail_observation=observation.fail,
                cancel_observation=observation.cancel,
            ),
            observation=observation,
            fatal_error_types=self.fatal_error_types,
            cancelled_error_types=self.cancelled_error_types,
        )

        async def on_turn_error(
            binding: LocalDaemonRuntimeBinding,
            exc: BaseException,
        ) -> None:
            del binding
            await turn_controller.on_turn_error(session, exc)

        async def execute_runtime() -> None:
            consumer = self.make_consumer(session)
            run_request = RunRequest(
                session_id=session.id,
                run_id=run_id,
                target=session.default_run_target,
                input_summary=prompt if prompt.strip() else None,
                resume_from_run_id=(
                    None if resume_context is None else resume_context.previous_run_id
                ),
            )
            await self.run_coordinator.submit_run(run_request)
            turn_controller.starter = RuntimeTurnStarter(
                turn_run=turn_run,
                consumer=consumer,
                run_id=run_id,
                prompt=prompt,
                bind_root_run_identity=self.bind_root_run_identity,
                bind_subagent_message_publisher=self.bind_subagent_message_publisher,
                start_observation=self.start_observation,
                resume_context=resume_context,
            )
            turn_controller.finalizer = self.runtime_run_persistence.turn_finalizer(
                persist_session=self.persist_session,
                complete_observation=observation.complete,
            )

            async def before_turn(binding: LocalDaemonRuntimeBinding) -> None:
                await turn_controller.before_turn(session, binding)

            async def after_turn(
                binding: LocalDaemonRuntimeBinding,
                outcome: object,
            ) -> None:
                await turn_controller.after_turn(session, binding, outcome)

            await self.run_coordinator.execute_runtime(
                LocalDaemonRuntimeExecution(
                    request=run_request,
                    runtime_provider=_RuntimeTurnLocalDaemonProvider(
                        prepare=lambda request: self.prepare_runtime(
                            session,
                            consumer=consumer,
                            request=request,
                        )
                    ),
                    prompt=prompt,
                    before_turn=before_turn,
                    after_turn=after_turn,
                    on_turn_error=on_turn_error,
                )
            )

        try:
            await turn_controller.run_execution(
                session,
                execute_runtime,
                ensure_started_error_types=(RunCoordinatorError,),
            )
        finally:
            await turn_session_state.finalize(
                session,
                current_task=current_task,
                turn_finished=True,
            )


__all__ = [
    "RuntimeProviderPreparer",
    "RuntimeTurnConsumerFactory",
    "RuntimeTurnService",
    "RuntimeTurnServiceSession",
]
