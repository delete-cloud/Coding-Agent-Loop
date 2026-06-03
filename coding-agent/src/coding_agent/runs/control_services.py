from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from coding_agent.runs.attached_executor import RuntimeAttachedExecutorService
from coding_agent.runs.cancel import RuntimeCancelService
from coding_agent.runs.lifecycle import RuntimeRunMetadataProvider, RuntimeTaskStopper
from coding_agent.runs.persistence import RuntimeRunPersistenceService
from coding_agent.runs.query import RuntimeQueryService
from coding_agent.runs.recovery import RuntimeRunRecoveryService
from coding_agent.runs.resume import RuntimeResumeService
from coding_agent.stores import RuntimeStore


RuntimeStoreProvider = Callable[[], RuntimeStore | None]
RuntimeOwnerProvider = Callable[[], str | None]
RuntimeSessionLister = Callable[[], Awaitable[list[str]]]
RuntimeSessionRecoverability = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class RuntimeControlServices:
    store: RuntimeStoreProvider
    metadata_for_session: RuntimeRunMetadataProvider
    list_session_ids: RuntimeSessionLister
    session_is_recoverable: RuntimeSessionRecoverability
    owner_id: RuntimeOwnerProvider
    active_resume_blocking_statuses: frozenset[str]

    def run_persistence(self) -> RuntimeRunPersistenceService:
        store = self.store()
        return RuntimeRunPersistenceService(
            run_store=store,
            checkpoint_store=store,
            metadata_for_session=self.metadata_for_session,
        )

    def run_recovery(self) -> RuntimeRunRecoveryService:
        return RuntimeRunRecoveryService(
            store=self.store(),
            list_session_ids=self.list_session_ids,
            session_is_recoverable=self.session_is_recoverable,
            owner_id=self.owner_id(),
        )

    def queries(self) -> RuntimeQueryService:
        return RuntimeQueryService(
            self.store(),
            active_resume_blocking_statuses=self.active_resume_blocking_statuses,
        )

    def resume(self) -> RuntimeResumeService:
        return RuntimeResumeService()

    def attached_executor(self) -> RuntimeAttachedExecutorService:
        return RuntimeAttachedExecutorService(
            store=self.store(),
            metadata_for_session=self.metadata_for_session,
        )

    def cancel(self) -> RuntimeCancelService:
        return RuntimeCancelService(store=self.store())

    def task_stopper(self) -> RuntimeTaskStopper:
        return RuntimeTaskStopper()


__all__ = [
    "RuntimeControlServices",
    "RuntimeOwnerProvider",
    "RuntimeSessionLister",
    "RuntimeSessionRecoverability",
    "RuntimeStoreProvider",
]
