from __future__ import annotations

from datetime import UTC, datetime

from coding_agent.events.connected_chat import build_root_settlement
from coding_agent.stores.runtime_store import AuthoritativeUnitOfWork, AgentRunRecord


def test_failed_root_settlement_structures_chat_error_without_losing_run_error() -> (
    None
):
    run = AgentRunRecord(
        run_id="run-01",
        session_id="session-01",
        tape_id="tape-01",
        parent_run_id=None,
        agent_id=None,
        status="running",
        started_at=datetime.now(UTC),
    )

    settlement = build_root_settlement(
        run=run,
        session_state={"id": "session-01", "tape_id": "tape-01"},
        outcome="failed",
        result=None,
        error="provider unavailable",
    )

    assert isinstance(settlement, AuthoritativeUnitOfWork)
    assert settlement.event is not None
    assert settlement.event.payload["error"] == {
        "code": "adapter_failed",
        "message": "provider unavailable",
    }
    assert settlement.run_state is not None
    assert settlement.run_state.error == "provider unavailable"
