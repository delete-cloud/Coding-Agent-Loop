from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from coding_agent.runtime_activation import (
    RUNTIME_VERSION_LEGACY,
    RUNTIME_VERSION_NEW,
    CrossVersionWriteError,
    NewRuntimeSettledWriteError,
    UnknownRuntimeVersionError,
    parse_runtime_version,
    runtime_path_for_version,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.stores.runtime_store import EffectLedgerSlot
from tests.coding_agent.test_harness_p2_fact_source import (
    OWNER_ID,
    SESSION_ID,
    SESSION_PAYLOAD,
    _open_store,
    _unit,
)


def test_missing_runtime_version_is_legacy() -> None:
    assert parse_runtime_version({}) == RUNTIME_VERSION_LEGACY
    assert runtime_path_for_version(RUNTIME_VERSION_LEGACY) == "legacy"
    assert runtime_path_for_version(RUNTIME_VERSION_NEW) == "new"


def test_unknown_runtime_version_fails_closed() -> None:
    with pytest.raises(UnknownRuntimeVersionError):
        parse_runtime_version({"runtime_version": "future-9"})


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_existing_and_migrated_sessions_remain_legacy(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await store.save_session(owner, dict(SESSION_PAYLOAD))
    loaded = await _load_session(store, SESSION_ID)
    assert loaded is not None
    assert parse_runtime_version(loaded) == RUNTIME_VERSION_LEGACY


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_sessions_stay_legacy_until_activation_flag(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    activation = await store.load_runtime_activation()
    assert activation.new_sessions_enabled is False
    owner, payload = await _fresh_session(store, store_kind, "session-legacy-create")
    await store.save_session(owner, payload)
    loaded = await _load_session(store, owner.session_id)
    assert loaded is not None
    assert loaded["runtime_version"] == RUNTIME_VERSION_LEGACY


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_sessions_receive_new_runtime_version_after_flag(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    owner, payload = await _fresh_session(store, store_kind, "session-new-create")
    await store.save_session(owner, payload)
    loaded = await _load_session(store, owner.session_id)
    assert loaded is not None
    assert loaded["runtime_version"] == RUNTIME_VERSION_NEW


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_child_run_inherits_parent_runtime_version(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    owner, payload = await _fresh_session(store, store_kind, "session-child-inherit")
    await store.save_session(owner, payload)
    parent = await _load_session(store, owner.session_id)
    assert parent is not None
    await store.save_session(
        owner,
        {
            **payload,
            "runtime_version": parent["runtime_version"],
            "parent_run_id": "matrix-parent-run",
        },
    )
    loaded = await _load_session(store, owner.session_id)
    assert loaded is not None
    assert loaded["runtime_version"] == RUNTIME_VERSION_NEW
    assert runtime_path_for_version(str(loaded["runtime_version"])) == "new"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_unknown_runtime_version_fails_closed_before_mutation(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    owner, payload = await _fresh_session(store, store_kind, "session-unknown")
    with pytest.raises(UnknownRuntimeVersionError):
        await store.save_session(
            owner,
            {**payload, "runtime_version": "not-a-version"},
        )
    assert await _load_session(store, owner.session_id) is None


@pytest.mark.asyncio
async def test_unknown_runtime_version_fails_closed_before_mutation_sqlite(
    tmp_path: Path,
) -> None:
    await test_unknown_runtime_version_fails_closed_before_mutation("sqlite", tmp_path)


@pytest.mark.asyncio
async def test_unknown_runtime_version_fails_closed_before_mutation_postgresql(
    tmp_path: Path,
) -> None:
    await test_unknown_runtime_version_fails_closed_before_mutation("pg", tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_cross_version_write_fails_closed_before_mutation(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, owner = await _open_store(store_kind, tmp_path)
    await store.save_session(owner, dict(SESSION_PAYLOAD))
    with pytest.raises(CrossVersionWriteError):
        await store.save_session(
            owner,
            {**SESSION_PAYLOAD, "runtime_version": RUNTIME_VERSION_NEW},
        )
    loaded = await _load_session(store, SESSION_ID)
    assert loaded is not None
    assert loaded["runtime_version"] == RUNTIME_VERSION_LEGACY


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_new_runtime_cannot_write_settled_or_use_legacy_terminal_writer(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    owner, payload = await _fresh_session(
        store, store_kind, "session-settled-forbidden"
    )
    await store.save_session(owner, payload)
    unit = replace(
        _unit(
            "settled-forbidden",
            session_state={
                **payload,
                "runtime_version": RUNTIME_VERSION_NEW,
                "turn": "settled-forbidden",
            },
        ),
        effect=EffectLedgerSlot(
            effect_id="effect-1",
            status="settled",
            payload={"attempt": "settled-forbidden"},
        ),
    )
    with pytest.raises(NewRuntimeSettledWriteError):
        await store.commit_authoritative_uow(owner, unit)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_legacy_session_keeps_pipeline_adapter_and_message_bus(
    store_kind: str,
    tmp_path: Path,
) -> None:
    del store_kind, tmp_path
    assert runtime_path_for_version(RUNTIME_VERSION_LEGACY) == "legacy"


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "pg"])
async def test_activation_rollback_restores_legacy_creation_without_mutating_versions(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store, _owner = await _open_store(store_kind, tmp_path)
    await store.set_new_session_runtime_activation(enabled=True)
    owner, payload = await _fresh_session(store, store_kind, "session-keep-new")
    await store.save_session(owner, payload)
    first = await _load_session(store, owner.session_id)
    await store.set_new_session_runtime_activation(enabled=False)
    still = await _load_session(store, owner.session_id)
    assert first is not None and still is not None
    assert first["runtime_version"] == RUNTIME_VERSION_NEW
    assert still["runtime_version"] == RUNTIME_VERSION_NEW
    other_owner, other_payload = await _fresh_session(
        store,
        store_kind,
        "session-rollback",
    )
    await store.save_session(other_owner, other_payload)
    created_after = await _load_session(store, other_owner.session_id)
    assert created_after is not None
    assert created_after["runtime_version"] == RUNTIME_VERSION_LEGACY


async def _fresh_session(
    store: Any,
    store_kind: str,
    session_id: str,
) -> tuple[OwnerAuthority, dict[str, object]]:
    payload: dict[str, object] = {
        "id": session_id,
        "session_id": session_id,
        "tape_id": None,
        "status": "active",
    }
    if store_kind == "pg":
        owner = OwnerAuthority(session_id, OWNER_ID, 1)
        store._harness_pool.seed_owner(owner)
        return owner, payload
    owner = await store.acquire_owner(session_id, OWNER_ID)
    return owner, payload


async def _load_session(store: Any, session_id: str) -> dict[str, object] | None:
    return cast(dict[str, object] | None, await store.load_session_payload(session_id))
