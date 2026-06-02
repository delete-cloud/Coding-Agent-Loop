from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.stores import RuntimeRunLifecycleStore

RuntimeRunStore = RuntimeRunLifecycleStore


class RuntimeRunSession(Protocol):
    id: str
    tape_id: str | None


class RuntimeRunResumeContext(Protocol):
    previous_run_id: str


class RuntimeRunMetadataProvider(Protocol):
    def __call__(
        self,
        session: RuntimeRunSession,
        *,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> JSONObject: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


__all__ = [
    "RuntimeRunLifecycle",
    "RuntimeRunLifecycleStore",
    "RuntimeRunMetadataProvider",
    "RuntimeRunResumeContext",
    "RuntimeRunSession",
    "RuntimeRunStore",
]
