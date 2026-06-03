from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.runs import RuntimeRunRecoveryService
from coding_agent.runs.recovery import (
    STALE_RUNTIME_RUN_ERROR,
    STALE_RUNTIME_RUN_RECOVERY_REASON,
)


class RecordingRuntimeRunRecoveryStore:
    def __init__(self) -> None:
        self.runs: list[AgentRunRecord] = []
        self.updated: list[dict[str, object]] = []

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.runs.append(record)
        return record

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord:
        self.updated.append(
            {
                "run_id": run_id,
                "status": status,
                "ended_at": ended_at,
                "metadata": metadata,
                "result": result,
                "error": error,
            }
        )
        for index, run in enumerate(self.runs):
            if run.run_id != run_id:
                continue
            updated = AgentRunRecord(
                run_id=run.run_id,
                session_id=run.session_id,
                tape_id=run.tape_id,
                parent_run_id=run.parent_run_id,
                agent_id=run.agent_id,
                status=status,
                started_at=run.started_at,
                ended_at=ended_at,
                metadata=metadata,
                result=result,
                error=error,
            )
            self.runs[index] = updated
            return updated
        raise AssertionError(f"missing run {run_id}")

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs if run.session_id == session_id]


@pytest.mark.asyncio
async def test_runtime_run_recovery_service_interrupts_stale_running_runs() -> None:
    store = RecordingRuntimeRunRecoveryStore()
    recovered_at = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    started_at = datetime(2026, 5, 19, 11, 0, tzinfo=UTC)
    store.runs.extend(
        [
            AgentRunRecord(
                run_id="run-stale",
                session_id="session-1",
                tape_id="tape-1",
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
                metadata={"provider_name": "test-provider"},
                result={"steps_taken": 1},
            ),
            AgentRunRecord(
                run_id="run-complete",
                session_id="session-1",
                tape_id="tape-1",
                parent_run_id=None,
                agent_id=None,
                status="completed",
                started_at=started_at,
                ended_at=recovered_at,
            ),
        ]
    )
    service = RuntimeRunRecoveryService(
        store=store,
        list_session_ids=lambda: _session_ids("session-1"),
        owner_id="owner-a",
    )

    recovered_count = await service.recover_stale_runtime_runs(
        recovered_at=recovered_at
    )

    assert recovered_count == 1
    assert store.updated == [
        {
            "run_id": "run-stale",
            "status": "interrupted",
            "ended_at": recovered_at,
            "metadata": {
                "provider_name": "test-provider",
                "reclaimable": True,
                "recovered_at": recovered_at.isoformat(),
                "recovery_reason": STALE_RUNTIME_RUN_RECOVERY_REASON,
                "recovered_by_owner_id": "owner-a",
            },
            "result": {"steps_taken": 1},
            "error": STALE_RUNTIME_RUN_ERROR,
        }
    ]


@pytest.mark.asyncio
async def test_runtime_run_recovery_service_expires_attached_executor_leases() -> None:
    store = RecordingRuntimeRunRecoveryStore()
    recovered_at = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    started_at = datetime(2026, 5, 19, 11, 0, tzinfo=UTC)
    store.runs.extend(
        [
            AgentRunRecord(
                run_id="run-expired",
                session_id="session-1",
                tape_id="tape-1",
                parent_run_id=None,
                agent_id=None,
                status="claimed",
                started_at=started_at,
                metadata={
                    "execution_binding_kind": "local_attached",
                    "lease_expires_at": (
                        recovered_at - timedelta(seconds=1)
                    ).isoformat(),
                    "executor_id": "executor-1",
                },
                result={"steps_taken": 2},
            ),
            AgentRunRecord(
                run_id="run-active",
                session_id="session-1",
                tape_id="tape-1",
                parent_run_id=None,
                agent_id=None,
                status="claimed",
                started_at=started_at,
                metadata={
                    "execution_binding_kind": "external_worker",
                    "lease_expires_at": (
                        recovered_at + timedelta(seconds=30)
                    ).isoformat(),
                },
            ),
        ]
    )
    service = RuntimeRunRecoveryService(
        store=store,
        list_session_ids=lambda: _session_ids("session-1"),
    )

    recovered_count = await service.recover_stale_runtime_runs(
        recovered_at=recovered_at
    )

    assert recovered_count == 1
    assert store.updated == [
        {
            "run_id": "run-expired",
            "status": "expired",
            "ended_at": None,
            "metadata": {
                "execution_binding_kind": "local_attached",
                "lease_expires_at": (recovered_at - timedelta(seconds=1)).isoformat(),
                "executor_id": "executor-1",
                "reclaimable": True,
                "recovered_at": recovered_at.isoformat(),
                "recovery_reason": "attached_executor_lease_expired",
                "legacy_recovery_reason": "external_worker_lease_expired",
                "previous_status": "claimed",
            },
            "result": {"steps_taken": 2},
            "error": "external worker lease expired",
        }
    ]


@pytest.mark.asyncio
async def test_runtime_run_recovery_service_filters_sessions_by_owner_callback() -> (
    None
):
    store = RecordingRuntimeRunRecoveryStore()
    recovered_at = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    started_at = datetime(2026, 5, 19, 11, 0, tzinfo=UTC)
    for session_id in ["session-owned", "session-foreign"]:
        store.runs.append(
            AgentRunRecord(
                run_id=f"run-{session_id}",
                session_id=session_id,
                tape_id=None,
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
            )
        )

    async def session_is_recoverable(session_id: str) -> bool:
        return session_id == "session-owned"

    service = RuntimeRunRecoveryService(
        store=store,
        list_session_ids=lambda: _session_ids("session-owned", "session-foreign"),
        session_is_recoverable=session_is_recoverable,
    )

    recovered_count = await service.recover_stale_runtime_runs(
        recovered_at=recovered_at
    )

    assert recovered_count == 1
    assert [update["run_id"] for update in store.updated] == ["run-session-owned"]


@pytest.mark.asyncio
async def test_runtime_run_recovery_service_skips_storeless_recovery() -> None:
    list_calls = 0

    async def list_session_ids() -> list[str]:
        nonlocal list_calls
        list_calls += 1
        return ["session-1"]

    service = RuntimeRunRecoveryService(
        store=None,
        list_session_ids=list_session_ids,
    )

    recovered_count = await service.recover_stale_runtime_runs(
        recovered_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    )

    assert recovered_count == 0
    assert list_calls == 0


async def _session_ids(*session_ids: str) -> list[str]:
    return list(session_ids)
