from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.runs import RuntimeCancelService


@dataclass
class FakeSession:
    current_turn_id: str | None = "run-1"
    turn_in_progress: bool = True
    turn_status: str = "running"
    last_activity: datetime = datetime(2026, 6, 3, 11, 59, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cancel_attached_requested_run_marks_run_cancelled_and_session_idle() -> (
    None
):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    store = FakeCancelStore(_run(status="requested"))
    session = FakeSession()
    service = RuntimeCancelService(store=store, now=lambda: now)

    result = await service.cancel_attached_executor_turn(session)

    assert result.turn_id == "run-1"
    assert result.status == "cancelled"
    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.last_activity == now
    assert store.updated == [
        {
            "run_id": "run-1",
            "status": "cancelled",
            "ended_at": now,
            "metadata": {"cancel_requested_at": now.isoformat()},
            "result": {},
            "error": "cancelled before claim",
        }
    ]


@pytest.mark.asyncio
async def test_cancel_attached_without_active_turn_marks_idle_without_store_update() -> (
    None
):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    store = FakeCancelStore(_run(status="requested"))
    session = FakeSession(turn_in_progress=False)
    service = RuntimeCancelService(store=store, now=lambda: now)

    result = await service.cancel_attached_executor_turn(session)

    assert result.turn_id == "run-1"
    assert result.status == "idle"
    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.last_activity == now
    assert store.updated == []


@pytest.mark.asyncio
async def test_cancel_attached_claimed_run_marks_run_cancelling_and_session_cancelling() -> (
    None
):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    store = FakeCancelStore(_run(status="claimed", error=None))
    session = FakeSession()
    service = RuntimeCancelService(store=store, now=lambda: now)

    result = await service.cancel_attached_executor_turn(session)

    assert result.turn_id == "run-1"
    assert result.status == "cancelling"
    assert session.turn_in_progress is True
    assert session.turn_status == "cancelling"
    assert session.last_activity == now
    assert store.updated == [
        {
            "run_id": "run-1",
            "status": "cancelling",
            "ended_at": None,
            "metadata": {"cancel_requested_at": now.isoformat()},
            "result": {},
            "error": None,
        }
    ]


@pytest.mark.parametrize("turn_status", ["cancelling", "cancelled", "failed"])
def test_cancel_idle_or_finished_local_turn_preserves_terminal_status(
    turn_status: str,
) -> None:
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    session = FakeSession(
        current_turn_id=f"run-{turn_status}",
        turn_in_progress=True,
        turn_status=turn_status,
    )
    service = RuntimeCancelService(store=None, now=lambda: now)

    result = service.cancel_idle_or_finished_local_turn(session)

    assert result.turn_id == f"run-{turn_status}"
    assert result.status == turn_status
    assert session.turn_in_progress is False
    assert session.turn_status == turn_status
    assert session.last_activity == now


def test_cancel_idle_or_finished_local_turn_defaults_other_status_to_idle() -> None:
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    session = FakeSession(
        current_turn_id="run-running",
        turn_in_progress=True,
        turn_status="running",
    )
    service = RuntimeCancelService(store=None, now=lambda: now)

    result = service.cancel_idle_or_finished_local_turn(session)

    assert result.turn_id == "run-running"
    assert result.status == "idle"
    assert session.turn_in_progress is False
    assert session.turn_status == "idle"
    assert session.last_activity == now


def _run(
    *,
    status: str,
    error: str | None = None,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id="run-1",
        session_id="session-1",
        tape_id="tape-1",
        parent_run_id=None,
        agent_id=None,
        status=status,
        started_at=datetime(2026, 6, 3, 11, 59, tzinfo=UTC),
        ended_at=None,
        metadata={},
        result={},
        error=error,
    )


class FakeCancelStore:
    def __init__(self, run: AgentRunRecord) -> None:
        self.run = run
        self.updated: list[dict[str, object]] = []

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        if run_id == self.run.run_id:
            return self.run
        return None

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.run = record
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
        self.run = AgentRunRecord(
            run_id=self.run.run_id,
            session_id=self.run.session_id,
            tape_id=self.run.tape_id,
            parent_run_id=self.run.parent_run_id,
            agent_id=self.run.agent_id,
            status=status,
            started_at=self.run.started_at,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )
        return self.run
