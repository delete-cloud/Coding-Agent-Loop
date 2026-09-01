from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from coding_agent.runtime_activation import (
    RUNTIME_VERSION_LEGACY,
    NewRuntimeCheckpointRejectedError,
    assert_checkpoint_allowed,
)
from tests.coding_agent.test_harness_p2_fact_source import (
    SESSION_ID,
    TAPE_ID,
    _open_store,
)
from tests.coding_agent.test_phase_f_runtime_version import _fresh_session, _load_session


def test_assert_checkpoint_allowed_rejects_new_runtime() -> None:
    assert_checkpoint_allowed({"id": "s", "runtime_version": RUNTIME_VERSION_LEGACY})
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        assert_checkpoint_allowed({"id": "s", "runtime_version": "agentkit-1"})


def _snapshot(session_id: str) -> CheckpointSnapshot:
    return CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="phase-f-checkpoint",
            tape_id=TAPE_ID,
            session_id=session_id,
            entry_count=0,
            window_start=0,
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
            label="phase-f",
        ),
        tape_entries=(),
        plugin_states={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_runtime_checkpoint_capture_rejects_before_mutation(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    session_id = "session-new-checkpoint"
    owner, payload = await _fresh_session(store, store_kind, session_id)
    await store.save_session(owner, payload)
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        await store.save_checkpoint(owner, _snapshot(session_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_runtime_checkpoint_restore_rejects_before_mutation(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    session_id = "session-new-restore"
    owner, payload = await _fresh_session(store, store_kind, session_id)
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        await store.restore_checkpoint_state(
            owner,
            _snapshot(session_id),
            {**payload, "tape_id": TAPE_ID, "runtime_version": "agentkit-1"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_legacy_checkpoint_capture_and_restore_unchanged(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    loaded = await _load_session(store, SESSION_ID)
    if loaded is None:
        await store.save_session(
            owner,
            {
                "id": SESSION_ID,
                "session_id": SESSION_ID,
                "tape_id": TAPE_ID,
                "status": "active",
            },
        )
        loaded = await _load_session(store, SESSION_ID)
    assert loaded is not None
    assert_checkpoint_allowed(loaded)
    if store_kind == "pg":
        return
    await store.save_session(owner, {**loaded, "tape_id": TAPE_ID})
    snapshot = _snapshot(SESSION_ID)
    await store.append_tape_entries(
        owner,
        TAPE_ID,
        [{"kind": "message", "payload": {"text": "keep"}}],
    )
    await store.save_checkpoint(owner, snapshot)
    payload = await _load_session(store, SESSION_ID)
    assert payload is not None
    await store.restore_checkpoint_state(
        owner, snapshot, {**payload, "tape_id": TAPE_ID}
    )
