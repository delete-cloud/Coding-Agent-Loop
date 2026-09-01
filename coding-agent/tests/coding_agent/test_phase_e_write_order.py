from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agentkit.runtime.contracts import (
    EffectMutation,
    EffectPlan,
    EffectStatus,
    OperationStateCAS,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.runtime_store import (
    AuthoritativeUnitOfWork,
    EffectMutationConflictError,
)
from tests.coding_agent.test_harness_p2_fact_source import (
    OWNER_ID,
    SESSION_ID,
    SESSION_PAYLOAD,
    _open_store,
)


class RecordingSQLiteStore(SQLiteLocalDurableStore):
    def __init__(self, path: Path) -> None:
        self.statements: list[str] = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        connection = super()._connect()
        connection.set_trace_callback(self.statements.append)
        return connection


@pytest.fixture(params=["sqlite", "pg"])
def store_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def _open_recording_store(store_kind: str, tmp_path: Path):
    if store_kind == "pg":
        store, owner = await _open_store("pg", tmp_path)
        store._harness_pool.connection.calls.clear()
        return store, owner
    store = RecordingSQLiteStore(tmp_path / "write-order.sqlite3")
    owner = await store.acquire_owner(SESSION_ID, OWNER_ID)
    await store.save_session(owner, SESSION_PAYLOAD)
    store.statements.clear()
    return store, owner


def _mutating_statements(store: Any) -> list[str]:
    if isinstance(store, RecordingSQLiteStore):
        statements = store.statements
    else:
        statements = [query for _kind, query in store._harness_pool.connection.calls]
    return [
        statement.strip()
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        and "session_fact_source" not in statement
    ]


def _state_only_unit(transition_id: str) -> AuthoritativeUnitOfWork:
    return AuthoritativeUnitOfWork(
        event=None,
        session_state=SESSION_PAYLOAD,
        transition_id=transition_id,
        state_cas=OperationStateCAS("write-order-run", 0, 0),
        state_value={"phase": "committed"},
    )


@pytest.mark.asyncio
async def test_exact_receipt_precedes_fresh_transition_writes(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_recording_store(store_kind, tmp_path)
    unit = _state_only_unit("receipt-first")

    first = await store.commit_authoritative_uow(owner, unit)
    writes = _mutating_statements(store)

    assert first.idempotent is False
    assert writes
    assert "session_transition_receipts" in writes[0]

    if isinstance(store, RecordingSQLiteStore):
        store.statements.clear()
    else:
        store._harness_pool.connection.calls.clear()
    replay = await store.commit_authoritative_uow(owner, unit)
    assert replay.idempotent is True
    assert _mutating_statements(store) == []


@pytest.mark.asyncio
async def test_effect_preconditions_precede_first_transition_write(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_recording_store(store_kind, tmp_path)
    first_plan = EffectPlan(
        effect_id="effect-present",
        attempt_id="attempt-present",
        effect_kind="tool",
        payload={"tool_name": "read", "arguments": {}},
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=SESSION_PAYLOAD,
            transition_id="prepare-present-effect",
            state_cas=OperationStateCAS("effect-order-run", 0, 0),
            state_value={"phase": "prepared"},
            effect_mutations=(EffectMutation.prepare(first_plan),),
            effect_plans=(first_plan,),
        ),
    )
    await store.commit_authoritative_uow(
        owner,
        AuthoritativeUnitOfWork(
            event=None,
            session_state=SESSION_PAYLOAD,
            transition_id="authorize-present-effect",
            state_cas=OperationStateCAS("effect-order-run", 1, 0),
            state_value={"phase": "dispatched"},
            effect_mutation=EffectMutation(
                effect_id=first_plan.effect_id,
                attempt_id=first_plan.attempt_id,
                expected_status=EffectStatus.PREPARED,
                status=EffectStatus.DISPATCHED,
                payload={},
            ),
            expected_mailbox_cut="0",
        ),
    )
    if isinstance(store, RecordingSQLiteStore):
        store.statements.clear()
    else:
        store._harness_pool.connection.calls.clear()

    invalid = AuthoritativeUnitOfWork(
        event=None,
        session_state=SESSION_PAYLOAD,
        transition_id="effect-precondition-failure",
        state_cas=OperationStateCAS("effect-order-run", 2, 0),
        state_value={"phase": "must-not-write"},
        effect_mutations=(
            EffectMutation(
                effect_id=first_plan.effect_id,
                attempt_id=first_plan.attempt_id,
                expected_status=EffectStatus.DISPATCHED,
                status=EffectStatus.COMPLETED,
                payload={},
            ),
            EffectMutation(
                effect_id="effect-missing",
                attempt_id="attempt-missing",
                expected_status=EffectStatus.DISPATCHED,
                status=EffectStatus.COMPLETED,
                payload={},
            ),
        ),
    )

    with pytest.raises(EffectMutationConflictError, match="does not exist"):
        await store.commit_authoritative_uow(owner, invalid)
    assert _mutating_statements(store) == []
