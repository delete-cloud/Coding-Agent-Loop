from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from coding_agent.runtime_activation import (
    CHECKPOINT_FORMAT_KEY,
    RUNTIME_VERSION_NEW,
    NewRuntimeCheckpointRejectedError,
    assert_checkpoint_allowed,
    is_new_runtime_restore_point,
)
from tests.coding_agent.test_harness_p2_fact_source import _open_store
from tests.coding_agent.test_phase_f_checkpoint_rejection import _snapshot
from tests.coding_agent.test_phase_f_runtime_version import (
    _fresh_session,
    _load_session,
)


def _restore_point(session_id: str, tape_id: str) -> CheckpointSnapshot:
    return CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id=f"g1-{session_id}",
            tape_id=tape_id,
            session_id=session_id,
            entry_count=0,
            window_start=0,
            created_at=datetime(2026, 9, 3, tzinfo=UTC),
            label="g1",
        ),
        tape_entries=(),
        plugin_states={},
        extra={CHECKPOINT_FORMAT_KEY: RUNTIME_VERSION_NEW},
    )


def test_restore_point_predicate_and_fence() -> None:
    payload = {"id": "s", "runtime_version": RUNTIME_VERSION_NEW}
    snapshot = _restore_point("s", "tape-s")
    assert is_new_runtime_restore_point(snapshot)
    assert_checkpoint_allowed(payload, snapshot)
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        assert_checkpoint_allowed(payload, _snapshot("s"))
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        assert_checkpoint_allowed({"id": "s", "runtime_version": "legacy"}, snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_runtime_restore_point_capture_persists_on_sqlite_and_pg(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    session_id = f"session-g1-capture-{store_kind}"
    tape_id = f"tape-g1-capture-{store_kind}"
    owner, payload = await _fresh_session(store, store_kind, session_id)
    await store.save_session(owner, {**payload, "tape_id": tape_id})
    snapshot = _restore_point(session_id, tape_id)
    await store.save_checkpoint(owner, snapshot)
    if store_kind == "sqlite":
        loaded = await store.load_checkpoint(snapshot.meta.checkpoint_id)
        assert loaded is not None
        assert is_new_runtime_restore_point(loaded)
        assert loaded.tape_entries == ()
        assert loaded.plugin_states == {}
    else:
        stored = await _load_session(store, session_id)
        assert stored is not None
        assert stored["runtime_version"] == RUNTIME_VERSION_NEW


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_runtime_restore_point_restore_rebuilds_session_without_plugin_states(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    session_id = f"session-g1-restore-{store_kind}"
    tape_id = f"tape-g1-restore-{store_kind}"
    owner, payload = await _fresh_session(store, store_kind, session_id)
    session_payload = {**payload, "tape_id": tape_id}
    await store.save_session(owner, session_payload)
    stored = await _load_session(store, session_id)
    assert stored is not None
    assert stored["runtime_version"] == RUNTIME_VERSION_NEW
    snapshot = _restore_point(session_id, tape_id)
    await store.save_checkpoint(owner, snapshot)
    await store.restore_checkpoint_state(owner, snapshot, stored)
    loaded = await _load_session(store, session_id)
    assert loaded is not None
    assert loaded["runtime_version"] == RUNTIME_VERSION_NEW
    assert loaded["tape_id"] == tape_id
    if store_kind == "sqlite":
        persisted = await store.load_checkpoint(snapshot.meta.checkpoint_id)
        assert persisted is not None
        assert persisted.plugin_states == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_runtime_rejects_tape_or_plugin_state_snapshot(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    session_id = f"session-g1-reject-{store_kind}"
    owner, payload = await _fresh_session(store, store_kind, session_id)
    await store.save_session(owner, payload)
    tape_snapshot = _snapshot(session_id)
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        await store.save_checkpoint(owner, tape_snapshot)
    plugin_snapshot = CheckpointSnapshot(
        meta=tape_snapshot.meta,
        tape_entries=(),
        plugin_states={"llm_provider": {"hot": True}},
        extra={CHECKPOINT_FORMAT_KEY: RUNTIME_VERSION_NEW},
    )
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        await store.save_checkpoint(owner, plugin_snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_cross_version_checkpoint_still_rejects_before_mutation(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    session_id = f"session-g1-cross-{store_kind}"
    tape_id = f"tape-g1-cross-{store_kind}"
    owner, payload = await _fresh_session(store, store_kind, session_id)
    await store.save_session(owner, {**payload, "tape_id": tape_id})
    with pytest.raises(NewRuntimeCheckpointRejectedError):
        await store.save_checkpoint(owner, _restore_point(session_id, tape_id))
