from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from coding_agent.stores.runtime_store import (
    AuthoritativeUnitOfWork,
    EventRecord,
    StateVersionConflictError,
)
from tests.coding_agent.test_harness_p2_fact_source import (
    SESSION_ID,
    SESSION_PAYLOAD,
    _open_store,
)
from tests.coding_agent.test_phase_f_runtime_version import _load_session


STAMP = datetime(2026, 9, 3, 16, 0, tzinfo=UTC)


def _event_unit(*, status: str = "active") -> AuthoritativeUnitOfWork:
    return AuthoritativeUnitOfWork(
        event=EventRecord(
            event_id="g2-event",
            session_id=SESSION_ID,
            event_kind="assistant_message",
            payload={"text": "g2"},
            created_at=STAMP,
        ),
        session_state={**SESSION_PAYLOAD, "status": status},
    )


def _bump_projection_epoch(store: Any, store_kind: str) -> None:
    if store_kind == "sqlite":
        with store._connect() as connection:
            connection.execute(
                """
                UPDATE session_fact_source
                SET projection_epoch = projection_epoch + 1
                WHERE session_id = ?
                """,
                (SESSION_ID,),
            )
        return
    row = store._harness_pool.connection.fact_source[SESSION_ID]
    row["projection_epoch"] = int(row["projection_epoch"]) + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_same_epoch_lost_ack_returns_original_event_without_write(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = await store.commit_authoritative_uow(owner, _event_unit())
    assert first.event is not None
    assert first.idempotent is False
    assert first.event.projection_epoch == "0"

    retry = await store.commit_authoritative_uow(owner, _event_unit(status="mutated"))
    assert retry.idempotent is True
    assert retry.event is not None
    assert retry.event.event_id == first.event.event_id
    assert retry.event.projection_epoch == first.event.projection_epoch
    assert retry.event.session_seq == first.event.session_seq
    loaded = await _load_session(store, SESSION_ID)
    assert loaded is not None
    assert loaded["status"] == "active"
    assert await store.load_event_record(SESSION_ID, "2") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_retry_after_restore_epoch_fails_cas_without_write(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    first = await store.commit_authoritative_uow(owner, _event_unit())
    assert first.event is not None
    original_epoch = first.event.projection_epoch
    _bump_projection_epoch(store, store_kind)

    with pytest.raises(StateVersionConflictError, match="projection_epoch"):
        await store.commit_authoritative_uow(owner, _event_unit(status="mutated"))

    loaded = await _load_session(store, SESSION_ID)
    assert loaded is not None
    assert loaded["status"] == "active"
    stored = await store.load_event_record(SESSION_ID, first.event.session_seq)
    assert stored is not None
    assert stored.projection_epoch == original_epoch
    assert await store.load_event_record(SESSION_ID, "2") is None
