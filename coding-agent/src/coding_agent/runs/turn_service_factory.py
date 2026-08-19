from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from agentkit.tools import FatalToolExecutionError

from coding_agent.runs.control_services import RuntimeControlServices
from coding_agent.runs.coordinator import RunCoordinator
from coding_agent.runs.lifecycle import (
    RuntimeObservationCompleter,
    RuntimeObservationStarter,
    RuntimeRootRunIdentityBinder,
    RuntimeSessionCloser,
    RuntimeSubagentMessagePublisherBinder,
    RuntimeTurnStatePersister,
)
from coding_agent.runs.turn_execution import (
    RuntimeProviderPreparer,
    RuntimeTurnConsumerFactory,
    RuntimeTurnService,
)
from coding_agent.wire.runtime import RuntimeTurnWireEmitter


@dataclass(frozen=True)
class RuntimeTurnServiceFactory:
    runtime_control_services: RuntimeControlServices
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
    fatal_error_types: tuple[type[BaseException], ...] = (FatalToolExecutionError,)
    cancelled_error_types: tuple[type[BaseException], ...] = (asyncio.CancelledError,)

    def build(self, run_coordinator: RunCoordinator) -> RuntimeTurnService:
        return RuntimeTurnService(
            run_coordinator=run_coordinator,
            runtime_run_persistence=self.runtime_control_services.run_persistence(),
            persist_session=self.persist_session,
            persist_turn_started=self.persist_turn_started,
            persist_turn_settled=self.persist_turn_settled,
            make_consumer=self.make_consumer,
            prepare_runtime=self.prepare_runtime,
            close_runtime=self.close_runtime,
            emit_message=self.emit_message,
            bind_root_run_identity=self.bind_root_run_identity,
            bind_subagent_message_publisher=self.bind_subagent_message_publisher,
            start_observation=self.start_observation,
            complete_observation=self.complete_observation,
            log_turn_exception=self.log_turn_exception,
            fatal_error_types=self.fatal_error_types,
            cancelled_error_types=self.cancelled_error_types,
        )


__all__ = ["RuntimeTurnServiceFactory"]
