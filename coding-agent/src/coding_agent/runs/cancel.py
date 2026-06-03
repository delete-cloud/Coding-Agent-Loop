from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, cast

from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.stores import RuntimeRunLifecycleStore


type RuntimeCancelStatus = Literal["idle", "cancelling", "cancelled", "failed"]


class RuntimeCancelStore(RuntimeRunLifecycleStore, Protocol):
    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None: ...


class RuntimeCancelSession(Protocol):
    current_turn_id: str | None
    turn_in_progress: bool
    turn_status: str
    last_activity: datetime


@dataclass(frozen=True)
class RuntimeCancelResult:
    turn_id: str | None
    status: RuntimeCancelStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


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

    async def _load_run(self, run_id: str) -> AgentRunRecord:
        if self.store is None:
            raise RuntimeError("runtime store is not configured")
        run = await self.store.load_agent_run(run_id)
        if run is None:
            raise KeyError(f"runtime run not found: {run_id}")
        return run


__all__ = [
    "RuntimeCancelResult",
    "RuntimeCancelService",
    "RuntimeCancelSession",
    "RuntimeCancelStore",
    "RuntimeCancelStatus",
]
