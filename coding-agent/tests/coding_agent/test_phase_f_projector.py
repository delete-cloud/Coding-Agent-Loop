from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.events.projector import project_takeover
from tests.coding_agent.test_harness_p2_fact_source import (
    SESSION_ID,
    _open_store,
    _unit,
)
from tests.coding_agent.test_phase_f_runtime_version import _load_session


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_projector_takeover_replay_creates_no_duplicate_wire_event(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    payload = await _load_session(store, SESSION_ID)
    if payload is None:
        await store.save_session(
            owner,
            {
                "id": SESSION_ID,
                "session_id": SESSION_ID,
                "status": "active",
            },
        )
        payload = await _load_session(store, SESSION_ID)
    assert payload is not None
    await store.commit_authoritative_uow(
        owner,
        _unit("projector-takeover", session_state={**payload, "turn": "projector"}),
    )
    first = await project_takeover(store, owner)
    second = await project_takeover(store, owner)
    ids = await store.list_wire_outbox_event_ids(SESSION_ID)
    assert first >= 1
    assert second == 0
    assert len(ids) == len(set(ids))
    assert len(ids) == first
