from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from coding_agent.stores.runtime_store import AgentRunRecord, JSONObject
from coding_agent.stores import RuntimeRunRecoveryStore

STALE_RUNTIME_RUN_ERROR = "runtime run was still running during startup recovery"
STALE_RUNTIME_RUN_RECOVERY_REASON = "startup_stale_running_run"
ATTACHED_EXECUTOR_REF_KINDS = frozenset({"external_worker", "local_attached"})
ATTACHED_EXECUTOR_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "interrupted", "cancelled"}
)
REMOTE_LOOP_RETIRED_RECOVERY_REASON = "remote_loop_ownership_retired"
REMOTE_LOOP_RETIRED_ERROR = (
    "remote loop ownership has been retired; the agent loop lives in-process"
)

type RuntimeRunSessionLister = Callable[[], Awaitable[list[str]]]
type RuntimeRunRecoveryEligibility = Callable[[str], Awaitable[bool]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _recover_all_sessions(session_id: str) -> bool:
    del session_id
    return True


@dataclass(frozen=True)
class RuntimeRunRecoveryService:
    store: RuntimeRunRecoveryStore | None
    list_session_ids: RuntimeRunSessionLister
    session_is_recoverable: RuntimeRunRecoveryEligibility = _recover_all_sessions
    owner_id: str | None = None
    now: Callable[[], datetime] = _utc_now

    async def recover_stale_runtime_runs(
        self,
        *,
        recovered_at: datetime | None = None,
    ) -> int:
        if self.store is None:
            return 0
        recovery_time = recovered_at or self.now()
        recovered_count = 0
        for session_id in await self.list_session_ids():
            if not await self.session_is_recoverable(session_id):
                continue
            runs = await self.store.list_agent_runs(session_id)
            for run in runs:
                if await self.recover_expired_attached_executor_run(
                    run,
                    recovered_at=recovery_time,
                ):
                    recovered_count += 1
                    continue
                if await self.recover_stale_running_run(
                    run,
                    recovered_at=recovery_time,
                ):
                    recovered_count += 1
        return recovered_count

    async def recover_stale_running_run(
        self,
        run: AgentRunRecord,
        *,
        recovered_at: datetime,
    ) -> bool:
        if self.store is None:
            return False
        if run.status != "running" or run.ended_at is not None:
            return False
        metadata = dict(run.metadata)
        metadata["reclaimable"] = True
        metadata["recovered_at"] = recovered_at.isoformat()
        metadata["recovery_reason"] = STALE_RUNTIME_RUN_RECOVERY_REASON
        if self.owner_id is not None:
            metadata["recovered_by_owner_id"] = self.owner_id
        await self.store.update_agent_run(
            run.run_id,
            status="interrupted",
            ended_at=recovered_at,
            metadata=cast(JSONObject, metadata),
            result=run.result,
            error=STALE_RUNTIME_RUN_ERROR,
        )
        return True

    async def recover_expired_attached_executor_run(
        self,
        run: AgentRunRecord,
        *,
        recovered_at: datetime,
    ) -> bool:
        if self.store is None:
            return False
        if run.metadata.get("executor_ref_kind") not in ATTACHED_EXECUTOR_REF_KINDS:
            return False
        if run.status in ATTACHED_EXECUTOR_TERMINAL_STATUSES:
            return False
        metadata = dict(run.metadata)
        metadata["reclaimable"] = False
        metadata["recovered_at"] = recovered_at.isoformat()
        metadata["recovery_reason"] = REMOTE_LOOP_RETIRED_RECOVERY_REASON
        metadata["previous_status"] = run.status
        if self.owner_id is not None:
            metadata["recovered_by_owner_id"] = self.owner_id
        await self.store.update_agent_run(
            run.run_id,
            status="interrupted",
            ended_at=recovered_at,
            metadata=cast(JSONObject, metadata),
            result=run.result,
            error=REMOTE_LOOP_RETIRED_ERROR,
        )
        return True


__all__ = [
    "ATTACHED_EXECUTOR_REF_KINDS",
    "ATTACHED_EXECUTOR_TERMINAL_STATUSES",
    "REMOTE_LOOP_RETIRED_ERROR",
    "REMOTE_LOOP_RETIRED_RECOVERY_REASON",
    "RuntimeRunRecoveryEligibility",
    "RuntimeRunRecoveryService",
    "RuntimeRunSessionLister",
    "STALE_RUNTIME_RUN_ERROR",
    "STALE_RUNTIME_RUN_RECOVERY_REASON",
]
