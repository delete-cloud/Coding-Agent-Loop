from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from agentkit.checkpoint import CheckpointService
from agentkit.storage.protocols import TapeStore
from agentkit.tape.tape import Tape

from coding_agent.executors import LocalDaemonExecutor
from coding_agent.runs.checkpoint_restore import (
    CheckpointRestoredRuntime,
    CheckpointRestoreService,
    CheckpointRestoreSession,
    CheckpointSessionConfig,
    CloseCheckpointRuntime,
    PersistCheckpointSession,
)
from coding_agent.runs.checkpoint_runtime import (
    CheckpointRuntimeAdapterFactory,
    CheckpointRuntimeBuilder,
    CheckpointRuntimeFactory,
    RestoreConsumerFactory,
    RuntimeEnvironmentResolver,
    RuntimePreparationRequestFactory,
    RuntimeWorkspaceRootResolver,
    SubagentMessagePublisherBinder,
)


RuntimeCheckpointRestoreAdmissionBody = Callable[[object], Awaitable[None]]


class RuntimeCheckpointRestoreAdmission(Protocol):
    async def run_exclusive(
        self,
        session_id: str,
        body: RuntimeCheckpointRestoreAdmissionBody,
    ) -> None: ...


RuntimeCheckpointRestoreOperation = Callable[
    [CheckpointRestoreSession, str],
    Awaitable[None],
]


@dataclass(frozen=True)
class RuntimeCheckpointRestoreOrchestrationService:
    admission: RuntimeCheckpointRestoreAdmission
    restore: RuntimeCheckpointRestoreOperation

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        async def restore_admitted_checkpoint(session: object) -> None:
            await self.restore(cast(CheckpointRestoreSession, session), checkpoint_id)

        await self.admission.run_exclusive(session_id, restore_admitted_checkpoint)


@dataclass(frozen=True)
class RuntimeCheckpointRestoreService:
    checkpoint_service: Callable[[], CheckpointService]
    tape_store: Callable[[], TapeStore]
    local_daemon_executor: LocalDaemonExecutor
    resolve_environment_for_run_target: RuntimeEnvironmentResolver
    workspace_root_for_environment: RuntimeWorkspaceRootResolver
    create_agent_for_session: CheckpointRuntimeFactory
    bind_subagent_message_publisher: SubagentMessagePublisherBinder
    restore_consumer_factory: RestoreConsumerFactory
    adapter_factory: CheckpointRuntimeAdapterFactory
    runtime_preparation_request: RuntimePreparationRequestFactory
    close_runtime: CloseCheckpointRuntime
    persist_session: PersistCheckpointSession

    async def restore(
        self,
        session: CheckpointRestoreSession,
        checkpoint_id: str,
    ) -> None:
        await CheckpointRestoreService(
            checkpoint_service=self.checkpoint_service(),
            tape_store=self.tape_store(),
            prepare_runtime=self._prepare_runtime,
            close_runtime=self.close_runtime,
            persist_session=self.persist_session,
        ).restore(session, checkpoint_id)

    async def _prepare_runtime(
        self,
        *,
        session: CheckpointRestoreSession,
        restored_tape: Tape,
        restored_config: CheckpointSessionConfig,
        plugin_states: Mapping[str, Any],
    ) -> CheckpointRestoredRuntime:
        return await CheckpointRuntimeBuilder(
            local_daemon_executor=self.local_daemon_executor,
            resolve_environment_for_run_target=self.resolve_environment_for_run_target,
            workspace_root_for_environment=self.workspace_root_for_environment,
            create_agent_for_session=self.create_agent_for_session,
            bind_subagent_message_publisher=self.bind_subagent_message_publisher,
            restore_consumer_factory=self.restore_consumer_factory,
            adapter_factory=self.adapter_factory,
            runtime_preparation_request=self.runtime_preparation_request,
        ).prepare_runtime(
            session=session,
            restored_tape=restored_tape,
            restored_config=restored_config,
            plugin_states=plugin_states,
        )


__all__ = [
    "RuntimeCheckpointRestoreAdmission",
    "RuntimeCheckpointRestoreAdmissionBody",
    "RuntimeCheckpointRestoreOperation",
    "RuntimeCheckpointRestoreOrchestrationService",
    "RuntimeCheckpointRestoreService",
]
