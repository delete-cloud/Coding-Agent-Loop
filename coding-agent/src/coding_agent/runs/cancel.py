from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.stores import RuntimeRunLifecycleStore


type RuntimeCancelStatus = Literal["idle", "cancelling", "cancelled", "failed"]


logger = logging.getLogger(__name__)


class RuntimeCancelStore(RuntimeRunLifecycleStore, Protocol):
    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None: ...


class RuntimeCancelSession(Protocol):
    id: str
    current_turn_id: str | None
    task: object | None
    turn_in_progress: bool
    turn_status: str
    last_activity: datetime


class RuntimeCancelableTask(Protocol):
    def done(self) -> bool: ...
    def cancel(self) -> bool: ...


RuntimeCancelServiceProvider = Callable[[], "RuntimeCancelService"]
RuntimeCancelSessionPersister = Callable[[RuntimeCancelSession], Awaitable[None]]
RuntimeCancelSessionAttachedPredicate = Callable[[RuntimeCancelSession], bool]
RuntimeCancelObservationScheduler = Callable[
    [str, RuntimeCancelableTask],
    None,
]
RuntimeCancelTurnIdFactory = Callable[[], str]
RuntimeCancelSessionLoader = Callable[[str], Awaitable[RuntimeCancelSession]]
RuntimeCancelSessionTaskMatcher = Callable[
    [RuntimeCancelSession, RuntimeCancelableTask],
    bool,
]
RuntimeCancelObservationLock = Callable[[], AbstractAsyncContextManager[object]]


@dataclass(frozen=True)
class RuntimeCancelResult:
    turn_id: str | None
    status: RuntimeCancelStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RuntimeCancelOrchestrationService:
    cancel_service: RuntimeCancelServiceProvider
    persist_session: RuntimeCancelSessionPersister
    session_is_attached: RuntimeCancelSessionAttachedPredicate
    schedule_cancel_observation: RuntimeCancelObservationScheduler
    turn_id_factory: RuntimeCancelTurnIdFactory

    async def cancel(
        self,
        session: RuntimeCancelSession,
        *,
        task: RuntimeCancelableTask | None,
    ) -> RuntimeCancelResult:
        service = self.cancel_service()
        if self.session_is_attached(session):
            result = await service.cancel_attached_executor_turn(session)
            await self.persist_session(session)
            return result
        if task is None or task.done():
            result = service.cancel_idle_or_finished_local_turn(session)
            await self.persist_session(session)
            return result

        if session.current_turn_id is None:
            session.current_turn_id = self.turn_id_factory()
        service.mark_cancelling(session)
        await self.persist_session(session)
        task.cancel()
        self.schedule_cancel_observation(session.id, task)
        return RuntimeCancelResult(
            turn_id=session.current_turn_id,
            status="cancelling",
        )


@dataclass(frozen=True)
class RuntimeCancelObservationFinalizer:
    cancel_service: RuntimeCancelServiceProvider
    load_session: RuntimeCancelSessionLoader
    persist_session: RuntimeCancelSessionPersister
    session_has_task: RuntimeCancelSessionTaskMatcher
    lock: RuntimeCancelObservationLock

    async def finalize(
        self,
        *,
        session_id: str,
        task: RuntimeCancelableTask,
    ) -> None:
        final_status = await self.cancel_service().observe_cancelled_local_task(task)

        async with self.lock():
            try:
                session = await self.load_session(session_id)
            except KeyError:
                return
            if not self.session_has_task(session, task):
                return
            session.task = None
            self.cancel_service().finish_observed_local_turn(
                session,
                status=final_status,
            )
            await self.persist_session(session)


@dataclass(frozen=True)
class RuntimeCancelService:
    store: RuntimeCancelStore | None
    now: Callable[[], datetime] = _utc_now

    async def cancel_attached_executor_turn(
        self,
        session: RuntimeCancelSession,
    ) -> RuntimeCancelResult:
        if session.current_turn_id is None or not session.turn_in_progress:
            self.mark_idle(session)
            return RuntimeCancelResult(
                turn_id=session.current_turn_id,
                status="idle",
            )
        if self.store is not None:
            run = await self._load_run(session.current_turn_id)
            metadata = dict(run.metadata)
            now = self.now()
            metadata["cancel_requested_at"] = now.isoformat()
            if run.status in {"requested", "expired"}:
                await self.store.update_agent_run(
                    run.run_id,
                    status="cancelled",
                    ended_at=now,
                    metadata=cast(JSONObject, metadata),
                    result=run.result,
                    error="cancelled before claim",
                )
                self.mark_idle(session)
                return RuntimeCancelResult(
                    turn_id=session.current_turn_id,
                    status="cancelled",
                )
            await self.store.update_agent_run(
                run.run_id,
                status="cancelling",
                ended_at=run.ended_at,
                metadata=cast(JSONObject, metadata),
                result=run.result,
                error=run.error,
            )
        self.mark_cancelling(session)
        return RuntimeCancelResult(
            turn_id=session.current_turn_id,
            status="cancelling",
        )

    def cancel_idle_or_finished_local_turn(
        self,
        session: RuntimeCancelSession,
    ) -> RuntimeCancelResult:
        if session.turn_status == "cancelling":
            status = "cancelling"
        elif session.turn_status in {"cancelled", "failed"}:
            status = session.turn_status
        else:
            session.turn_status = "idle"
            status = "idle"
        session.turn_in_progress = False
        session.last_activity = self.now()
        return RuntimeCancelResult(
            turn_id=session.current_turn_id,
            status=status,
        )

    def mark_cancelling(self, session: RuntimeCancelSession) -> None:
        session.turn_status = "cancelling"
        session.turn_in_progress = True
        session.last_activity = self.now()

    def mark_idle(self, session: RuntimeCancelSession) -> None:
        session.turn_status = "idle"
        session.turn_in_progress = False
        session.last_activity = self.now()

    async def observe_cancelled_local_task(
        self,
        task: Awaitable[Any],
    ) -> RuntimeCancelStatus:
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        except Exception:
            logger.exception("Cancelled session turn failed during cleanup")
            return "failed"
        return "cancelled"

    def finish_observed_local_turn(
        self,
        session: RuntimeCancelSession,
        *,
        status: RuntimeCancelStatus,
    ) -> None:
        if status not in {"cancelled", "failed"}:
            raise ValueError(f"invalid observed cancellation status: {status}")
        session.turn_in_progress = False
        session.turn_status = status
        session.last_activity = self.now()

    async def _load_run(self, run_id: str) -> AgentRunRecord:
        if self.store is None:
            raise RuntimeError("runtime store is not configured")
        run = await self.store.load_agent_run(run_id)
        if run is None:
            raise KeyError(f"runtime run not found: {run_id}")
        return run


__all__ = [
    "RuntimeCancelableTask",
    "RuntimeCancelObservationFinalizer",
    "RuntimeCancelObservationLock",
    "RuntimeCancelObservationScheduler",
    "RuntimeCancelOrchestrationService",
    "RuntimeCancelResult",
    "RuntimeCancelService",
    "RuntimeCancelServiceProvider",
    "RuntimeCancelSession",
    "RuntimeCancelSessionAttachedPredicate",
    "RuntimeCancelSessionLoader",
    "RuntimeCancelSessionPersister",
    "RuntimeCancelSessionTaskMatcher",
    "RuntimeCancelStore",
    "RuntimeCancelStatus",
    "RuntimeCancelTurnIdFactory",
]
