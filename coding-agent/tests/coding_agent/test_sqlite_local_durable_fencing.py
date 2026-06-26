from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.checkpoint import CheckpointService
from agentkit.errors import ConfigError
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.local import local_sqlite_path, local_sqlite_storage_config
from coding_agent.stores.runtime_store import AgentRunRecord, RuntimeEventRecord
from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_owner_store import (
    SQLiteSessionOwnerStore,
    SessionOwnershipConflictError,
)
from coding_agent.server.stores.session_store import InMemorySessionStore
from coding_agent.server.stores.session_store import SQLiteSessionStore
from coding_agent.topics.store import (
    SQLiteTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)


class FakeCheckpointStore:
    async def save(self, snapshot: CheckpointSnapshot) -> None:
        del snapshot

    async def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        del checkpoint_id
        return None

    async def list_by_tape(self, tape_id: str) -> list[CheckpointMeta]:
        del tape_id
        return []

    async def delete(self, checkpoint_id: str) -> None:
        del checkpoint_id


class FakeTapeStore:
    async def save(self, tape_id: str, entries: list[dict[str, object]]) -> None:
        del tape_id, entries

    async def load(self, tape_id: str) -> list[dict[str, object]]:
        del tape_id
        return []


@pytest.mark.asyncio
async def test_sqlite_owner_authority_renew_does_not_advance_epoch(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionOwnerStore(tmp_path / "local.sqlite3")

    authority = await store.acquire_authority(
        "session-a",
        "owner-a",
        lease_seconds=30.0,
    )
    renewed = await store.renew_authority(authority, lease_seconds=30.0)

    owner = await store.get_owner("session-a")
    assert renewed == authority
    assert owner is not None
    assert owner.owner_id == "owner-a"
    assert owner.fencing_token == authority.epoch == 1


@pytest.mark.asyncio
async def test_sqlite_owner_authority_takeover_advances_epoch_in_database(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionOwnerStore(tmp_path / "local.sqlite3")

    dead = await store.acquire_authority(
        "session-a",
        "owner-dead",
        lease_seconds=0.001,
    )
    await asyncio.sleep(0.01)

    taken = await store.acquire_authority(
        "session-a",
        "owner-b",
        lease_seconds=30.0,
    )

    assert dead.epoch == 1
    assert taken.owner_id == "owner-b"
    assert taken.epoch == 2


@pytest.mark.asyncio
async def test_sqlite_owner_authority_same_owner_expired_reacquire_advances_epoch(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionOwnerStore(tmp_path / "local.sqlite3")

    stale = await store.acquire_authority(
        "session-a",
        "owner-a",
        lease_seconds=0.001,
    )
    await asyncio.sleep(0.01)
    reacquired = await store.acquire_authority(
        "session-a",
        "owner-a",
        lease_seconds=30.0,
    )

    assert stale.epoch == 1
    assert reacquired.epoch == 2


def test_session_manager_partial_sqlite_backend_fails_loudly(
    tmp_path: Path,
) -> None:
    config = local_sqlite_storage_config(tmp_path)
    config["runtime_backend"] = "jsonl"
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))

    with pytest.raises(ConfigError) as exc_info:
        SessionManager(
            storage_config=config,
            owner_store=owner_store,
            owner_id="owner-a",
            fencing_token=999,
        )

    message = str(exc_info.value)
    assert "durable fencing requires all local sqlite backends" in message
    assert "runtime_backend='jsonl'" in message


def test_session_manager_sqlite_path_mismatch_fails_loudly(
    tmp_path: Path,
) -> None:
    config = local_sqlite_storage_config(tmp_path)
    config["runtime_path"] = str(tmp_path / "runtime.sqlite3")
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))

    with pytest.raises(ConfigError) as exc_info:
        SessionManager(
            storage_config=config,
            owner_store=owner_store,
            owner_id="owner-a",
            fencing_token=999,
        )

    message = str(exc_info.value)
    assert "durable fencing requires sqlite storage paths to share" in message
    assert "runtime_path" in message
    assert "runtime.sqlite3" in message


def test_session_manager_custom_tape_store_warns_and_disables_local_fencing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))

    with caplog.at_level("WARNING", logger="coding_agent.server.session_manager"):
        manager = SessionManager(
            storage_config=local_sqlite_storage_config(tmp_path),
            tape_store=FakeTapeStore(),
            owner_store=owner_store,
            owner_id="owner-a",
            fencing_token=999,
        )

    assert manager._local_durable_store is None
    assert "durable fencing disabled: custom tape_store supplied" in caplog.text


def test_session_manager_selects_sqlite_topic_store_for_local_durable_bundle(
    tmp_path: Path,
) -> None:
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))
    manager = SessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )

    selected = manager.selected_topic_store()

    assert isinstance(selected, SQLiteTopicStore)


def test_sqlite_local_durable_upgrade_preserves_existing_data_and_adds_topic_schema(
    tmp_path: Path,
) -> None:
    path = local_sqlite_path(tmp_path)
    SQLiteSessionStore(path).save(
        "session-existing",
        {"id": "session-existing", "tape_id": "tape-existing"},
    )

    SQLiteLocalDurableStore(path)

    assert SQLiteSessionStore(path).load("session-existing") == {
        "id": "session-existing",
        "tape_id": "tape-existing",
    }
    with sqlite3.connect(path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert {"topics", "topic_anchors", "topic_recall_links", "topic_costs"} <= (
        table_names
    )
    assert "topics_one_open_per_session_tape_idx" in index_names


@pytest.mark.asyncio
async def test_fenced_sqlite_topic_mutations_reject_stale_owner_for_all_mutators(
    tmp_path: Path,
) -> None:
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))
    manager = SessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_a = await manager.get_session_async(await manager.create_session())
    session_b = await manager.get_session_async(await manager.create_session())
    session_a.tape_id = "tape-a"
    session_b.tape_id = "tape-b"
    await manager._persist_session_async(session_a)
    await manager._persist_session_async(session_b)
    stale_store = manager.selected_topic_store()
    assert stale_store is not None
    await stale_store.create_topic(_topic("topic-1", session_a.id, session_a.tape_id))
    await stale_store.create_topic(
        _topic("topic-old", session_b.id, session_b.tape_id, seq=1)
    )
    await stale_store.finalize_topic(
        "topic-old",
        summary="old",
        topic_finalized_seq=3,
        finalized_at=datetime.now(UTC),
        metadata={},
    )
    await stale_store.create_topic(
        _topic("topic-2", session_b.id, session_b.tape_id, seq=4)
    )
    assert await owner_store.release(session_a.id, "owner-a", 1) is True
    assert await owner_store.release(session_b.id, "owner-a", 1) is True
    await owner_store.acquire_authority(session_a.id, "owner-b", lease_seconds=30.0)
    await owner_store.acquire_authority(session_b.id, "owner-b", lease_seconds=30.0)

    with pytest.raises(SessionOwnershipConflictError):
        await stale_store.finalize_topic(
            "topic-1",
            summary="done",
            topic_finalized_seq=5,
            finalized_at=datetime.now(UTC),
            metadata={},
        )
    with pytest.raises(SessionOwnershipConflictError):
        await stale_store.record_topic_anchor(
            TopicAnchorRecord(
                topic_id="topic-1",
                tape_id=session_a.tape_id,
                seq=1,
                anchor_type="topic_initial",
                entry_id=None,
            )
        )
    with pytest.raises(SessionOwnershipConflictError):
        await stale_store.abort_topic(
            "topic-2",
            summary="aborted",
            topic_finalized_seq=6,
            finalized_at=datetime.now(UTC),
            metadata={},
        )
    with pytest.raises(SessionOwnershipConflictError):
        await stale_store.record_recall_link(
            TopicRecallLinkRecord(
                source_topic_id="topic-2",
                recalled_topic_id="topic-old",
                relation="summary_recall",
            )
        )
    with pytest.raises(SessionOwnershipConflictError):
        await stale_store.update_topic_cost(TopicCostRecord(topic_id="topic-1"))


@pytest.mark.asyncio
async def test_fenced_sqlite_topic_mutations_reject_cross_session_targets(
    tmp_path: Path,
) -> None:
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))
    manager = SessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_a = await manager.get_session_async(await manager.create_session())
    session_b = await manager.get_session_async(await manager.create_session())
    session_a.tape_id = "tape-a"
    session_b.tape_id = "tape-b"
    await manager._persist_session_async(session_a)
    await manager._persist_session_async(session_b)
    topic_store = manager.selected_topic_store()
    assert topic_store is not None
    await topic_store.create_topic(_topic("topic-1", session_a.id, session_a.tape_id))

    with pytest.raises(SessionOwnershipConflictError):
        await topic_store.create_topic(
            _topic("topic-2", session_b.id, session_a.tape_id)
        )


def _topic(
    topic_id: str,
    session_id: str,
    tape_id: str,
    *,
    seq: int = 1,
) -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        tape_id=tape_id,
        session_id=session_id,
        kind="coding",
        status="open",
        title="Topic",
        summary=None,
        owner="local",
        topic_initial_seq=seq,
        topic_finalized_seq=None,
        created_at=datetime.now(UTC),
        finalized_at=None,
        metadata={},
    )


def test_session_manager_custom_tape_store_does_not_warn_for_non_sqlite_config(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner_store = SQLiteSessionOwnerStore(tmp_path / "owners.sqlite3")

    with caplog.at_level("WARNING", logger="coding_agent.server.session_manager"):
        manager = SessionManager(
            storage_config={
                "http_session_backend": "file",
                "tape_backend": "jsonl",
                "checkpoint_backend": "fs",
                "runtime_backend": "jsonl",
            },
            tape_store=FakeTapeStore(),
            owner_store=owner_store,
            owner_id="owner-a",
            fencing_token=999,
        )

    assert manager._local_durable_store is None
    assert "durable fencing disabled" not in caplog.text


def test_session_manager_partial_sqlite_with_custom_store_fails_loudly(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = local_sqlite_storage_config(tmp_path)
    config["runtime_backend"] = "jsonl"
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))

    with caplog.at_level("WARNING", logger="coding_agent.server.session_manager"):
        with pytest.raises(ConfigError) as exc_info:
            SessionManager(
                storage_config=config,
                tape_store=FakeTapeStore(),
                owner_store=owner_store,
                owner_id="owner-a",
                fencing_token=999,
            )

    assert "runtime_backend='jsonl'" in str(exc_info.value)
    assert "durable fencing disabled" not in caplog.text


@pytest.mark.asyncio
async def test_session_manager_uses_sqlite_owner_authority_epochs(
    tmp_path: Path,
) -> None:
    owner_store = SQLiteSessionOwnerStore(tmp_path / "local.sqlite3")
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )

    session_id = await manager.create_session()
    owner = await owner_store.get_owner(session_id)

    assert owner is not None
    assert owner.owner_id == "owner-a"
    assert owner.fencing_token == 1

    await manager.renew_owner_leases()
    renewed = await owner_store.get_owner(session_id)

    assert renewed is not None
    assert renewed.owner_id == "owner-a"
    assert renewed.fencing_token == 1

    await manager.release_owned_sessions()
    assert await owner_store.get_owner(session_id) is None


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_uses_protected_session_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    sqlite_path = local_sqlite_path(tmp_path)
    owner_store = SQLiteSessionOwnerStore(sqlite_path)
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )

    assert manager._local_durable_store is not None

    session_id = await manager.create_session()

    owner = await owner_store.get_owner(session_id)
    payload = SQLiteSessionStore(sqlite_path).load(session_id)

    assert owner is not None
    assert owner.fencing_token == 1
    assert payload is not None
    assert payload["id"] == session_id
    assert payload["tape_id"] is None


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_fences_session_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    sqlite_path = local_sqlite_path(tmp_path)
    owner_store = SQLiteSessionOwnerStore(sqlite_path)
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_id = await manager.create_session()
    assert await owner_store.release(session_id, "owner-a", 1) is True
    await owner_store.acquire_authority(session_id, "owner-b", lease_seconds=30.0)

    with pytest.raises(SessionOwnershipConflictError):
        await manager.remove_session_async(session_id)

    assert SQLiteSessionStore(sqlite_path).load(session_id) is not None


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_create_rollback_deletes_before_owner_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    sqlite_path = local_sqlite_path(tmp_path)
    owner_store = SQLiteSessionOwnerStore(sqlite_path)
    session_store = SQLiteSessionStore(sqlite_path)
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    release_observations: list[tuple[str, bool]] = []
    original_release = manager._release_owner_lease_for_session

    async def fail_workspace_persist(session) -> None:
        raise RuntimeError(f"workspace persist failed: {session.id}")

    async def observe_release(session_id: str) -> None:
        release_observations.append(
            (session_id, session_store.load(session_id) is None)
        )
        await original_release(session_id)

    monkeypatch.setattr(
        manager,
        "_persist_workspace_record_for_session",
        fail_workspace_persist,
    )
    monkeypatch.setattr(
        manager,
        "_release_owner_lease_for_session",
        observe_release,
    )

    with pytest.raises(RuntimeError, match="workspace persist failed"):
        await manager.create_session()

    assert release_observations
    session_id, session_deleted_before_release = release_observations[0]
    assert session_deleted_before_release is True
    assert session_store.load(session_id) is None
    assert await owner_store.get_owner(session_id) is None


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_rejects_sync_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    owner_store = SQLiteSessionOwnerStore(local_sqlite_path(tmp_path))
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_id = await manager.create_session()
    session = await manager.get_session_async(session_id)

    with pytest.raises(RuntimeError, match="synchronous session persistence"):
        manager._persist_session(session)
    with pytest.raises(RuntimeError, match="synchronous session removal"):
        manager.remove_session(session_id)
    with pytest.raises(RuntimeError, match="synchronous session clearing"):
        manager.clear_sessions()


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_fences_tape_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    sqlite_path = local_sqlite_path(tmp_path)
    owner_store = SQLiteSessionOwnerStore(sqlite_path)
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_id = await manager.create_session()
    session = await manager.get_session_async(session_id)
    session.tape_id = "tape-a"
    await manager._persist_session_async(session)

    await manager._tape_store.save(
        "tape-a",
        [{"kind": "message", "payload": {"text": "before takeover"}}],
    )

    assert await owner_store.release("session-missing", "owner-a", 1) is False
    assert await owner_store.release(session_id, "owner-a", 1) is True
    await owner_store.acquire_authority(session_id, "owner-b", lease_seconds=30.0)

    with pytest.raises(SessionOwnershipConflictError):
        await manager._tape_store.save(
            "tape-a",
            [{"kind": "message", "payload": {"text": "stale owner"}}],
        )

    assert await manager._tape_store.load("tape-a") == [
        {"kind": "message", "payload": {"text": "before takeover"}}
    ]


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_fences_runtime_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    sqlite_path = local_sqlite_path(tmp_path)
    owner_store = SQLiteSessionOwnerStore(sqlite_path)
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_id = await manager.create_session()
    session = await manager.get_session_async(session_id)
    session.tape_id = "tape-a"
    await manager._persist_session_async(session)

    started_at = datetime.now(UTC)
    await manager._runtime_store.create_agent_run(
        AgentRunRecord(
            run_id="run-a",
            session_id=session_id,
            tape_id="tape-a",
            parent_run_id=None,
            agent_id="agent-a",
            status="running",
            started_at=started_at,
        )
    )
    await manager._runtime_store.append_runtime_event(
        RuntimeEventRecord(
            event_id="event-before",
            run_id="run-a",
            event_kind="progress",
            payload={"step": "before"},
            created_at=started_at,
        )
    )

    assert await owner_store.release(session_id, "owner-a", 1) is True
    await owner_store.acquire_authority(session_id, "owner-b", lease_seconds=30.0)

    with pytest.raises(SessionOwnershipConflictError):
        await manager._runtime_store.append_runtime_event(
            RuntimeEventRecord(
                event_id="event-after",
                run_id="run-a",
                event_kind="progress",
                payload={"step": "after"},
                created_at=started_at + timedelta(seconds=1),
            )
        )

    assert [
        event.event_id
        for event in await manager._runtime_store.replay_runtime_events("run-a")
    ] == ["event-before"]


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_fences_runtime_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    sqlite_path = local_sqlite_path(tmp_path)
    owner_store = SQLiteSessionOwnerStore(sqlite_path)
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_id = await manager.create_session()
    session = await manager.get_session_async(session_id)
    session.tape_id = "tape-a"
    await manager._persist_session_async(session)
    await manager._runtime_store.create_agent_run(
        AgentRunRecord(
            run_id="run-a",
            session_id=session_id,
            tape_id="tape-a",
            parent_run_id=None,
            agent_id=None,
            status="requested",
            started_at=datetime.now(UTC),
            metadata={
                "executor_ref_kind": "external_worker",
                "executor_kind": "test-worker",
            },
        )
    )

    assert await owner_store.release(session_id, "owner-a", 1) is True
    await owner_store.acquire_authority(session_id, "owner-b", lease_seconds=30.0)

    assert (
        await manager._runtime_store.claim_external_worker_run(
            session_id=None,
            executor_kind="test-worker",
            claim_metadata={"worker_id": "worker-a"},
        )
        is None
    )
    assert (
        await manager._runtime_store.claim_external_worker_run(
            session_id=session_id,
            executor_kind="test-worker",
            claim_metadata={"worker_id": "worker-a"},
        )
        is None
    )

    run = await manager._runtime_store.load_agent_run("run-a")
    assert run is not None
    assert run.status == "requested"


@pytest.mark.asyncio
async def test_session_manager_default_sqlite_bundle_fences_checkpoint_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    sqlite_path = local_sqlite_path(tmp_path)
    owner_store = SQLiteSessionOwnerStore(sqlite_path)
    manager = SessionManager(
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=999,
    )
    session_id = await manager.create_session()
    session = await manager.get_session_async(session_id)
    session.tape_id = "tape-a"
    await manager._persist_session_async(session)
    created_at = datetime.now(UTC)

    checkpoint = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="checkpoint-a",
            tape_id="tape-a",
            session_id=session_id,
            entry_count=0,
            window_start=0,
            created_at=created_at,
            label="before",
        ),
        tape_entries=(),
        plugin_states={},
    )
    await manager._checkpoint_service._store.save(checkpoint)

    assert await owner_store.release(session_id, "owner-a", 1) is True
    await owner_store.acquire_authority(session_id, "owner-b", lease_seconds=30.0)

    with pytest.raises(SessionOwnershipConflictError):
        await manager._checkpoint_service._store.save(
            CheckpointSnapshot(
                meta=CheckpointMeta(
                    checkpoint_id="checkpoint-a",
                    tape_id="tape-a",
                    session_id=session_id,
                    entry_count=1,
                    window_start=0,
                    created_at=created_at + timedelta(seconds=1),
                    label="after",
                ),
                tape_entries=({"kind": "message"},),
                plugin_states={},
            )
        )

    loaded = await manager._checkpoint_service._store.load("checkpoint-a")
    assert loaded is not None
    assert loaded.meta.entry_count == 0
    assert loaded.meta.label == "before"


@pytest.mark.asyncio
async def test_sqlite_local_durable_store_rejects_stale_epoch_for_session_metadata(
    tmp_path: Path,
) -> None:
    store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
    stale = await store.acquire_owner("session-a", "owner-a", lease_seconds=0.001)
    await asyncio.sleep(0.01)
    current = await store.acquire_owner("session-a", "owner-b", lease_seconds=30.0)

    with pytest.raises(SessionOwnershipConflictError):
        await store.save_session(
            stale, {"id": "session-a", "session_id": "session-a", "tape_id": "tape-a"}
        )

    await store.save_session(
        current,
        {"id": "session-a", "session_id": "session-a", "tape_id": "tape-a"},
    )
    assert store.load_session("session-a") == {
        "id": "session-a",
        "session_id": "session-a",
        "tape_id": "tape-a",
    }


@pytest.mark.asyncio
async def test_sqlite_local_durable_store_rejects_session_payload_id_mismatch(
    tmp_path: Path,
) -> None:
    store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
    owner_a = await store.acquire_owner("session-a", "owner-a")

    with pytest.raises(SessionOwnershipConflictError):
        await store.save_session(
            owner_a,
            {"id": "session-b", "session_id": "session-a", "tape_id": "tape-a"},
        )


@pytest.mark.asyncio
async def test_sqlite_local_durable_store_rejects_cross_session_tape_write(
    tmp_path: Path,
) -> None:
    store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
    owner_a = await store.acquire_owner("session-a", "owner-a")
    owner_b = await store.acquire_owner("session-b", "owner-b")

    await store.save_session(
        owner_a,
        {"id": "session-a", "session_id": "session-a", "tape_id": "tape-a"},
    )
    await store.save_session(
        owner_b,
        {"id": "session-b", "session_id": "session-b", "tape_id": "tape-b"},
    )
    await store.append_tape_entries(
        owner_a,
        "tape-a",
        [{"kind": "message", "payload": {"text": "owned by session-a"}}],
    )

    with pytest.raises(SessionOwnershipConflictError):
        await store.append_tape_entries(
            owner_b,
            "tape-a",
            [{"kind": "message", "payload": {"text": "wrong owner"}}],
        )

    assert await store.load_tape("tape-a") == [
        {"kind": "message", "payload": {"text": "owned by session-a"}}
    ]


@pytest.mark.asyncio
async def test_sqlite_local_durable_store_rejects_cross_session_runtime_write(
    tmp_path: Path,
) -> None:
    store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
    owner_a = await store.acquire_owner("session-a", "owner-a")
    owner_b = await store.acquire_owner("session-b", "owner-b")
    started_at = datetime.now(UTC)

    run = AgentRunRecord(
        run_id="run-a",
        session_id="session-a",
        tape_id="tape-a",
        parent_run_id=None,
        agent_id="agent-a",
        status="running",
        started_at=started_at,
    )
    await store.create_agent_run(owner_a, run)

    with pytest.raises(SessionOwnershipConflictError):
        await store.append_runtime_event(
            owner_b,
            RuntimeEventRecord(
                event_id="event-wrong-owner",
                run_id="run-a",
                event_kind="progress",
                payload={"step": "wrong-owner"},
                created_at=started_at,
            ),
        )

    await store.append_runtime_event(
        owner_a,
        RuntimeEventRecord(
            event_id="event-owner-a",
            run_id="run-a",
            event_kind="progress",
            payload={"step": "owner-a"},
            created_at=started_at + timedelta(seconds=1),
        ),
    )
    assert [event.event_id for event in await store.replay_runtime_events("run-a")] == [
        "event-owner-a"
    ]


@pytest.mark.asyncio
async def test_sqlite_local_durable_store_rejects_checkpoint_rebind(
    tmp_path: Path,
) -> None:
    store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
    owner_a = await store.acquire_owner("session-a", "owner-a")
    owner_b = await store.acquire_owner("session-b", "owner-b")
    created_at = datetime.now(UTC)

    await store.save_session(
        owner_a,
        {"id": "session-a", "session_id": "session-a", "tape_id": "tape-a"},
    )
    await store.save_session(
        owner_b,
        {"id": "session-b", "session_id": "session-b", "tape_id": "tape-b"},
    )
    await store.save_checkpoint(
        owner_a,
        CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id="checkpoint-a",
                tape_id="tape-a",
                session_id="session-a",
                entry_count=0,
                window_start=0,
                created_at=created_at,
                label="owned",
            ),
            tape_entries=(),
            plugin_states={},
        ),
    )

    with pytest.raises(SessionOwnershipConflictError):
        await store.save_checkpoint(
            owner_b,
            CheckpointSnapshot(
                meta=CheckpointMeta(
                    checkpoint_id="checkpoint-a",
                    tape_id="tape-b",
                    session_id="session-b",
                    entry_count=0,
                    window_start=0,
                    created_at=created_at,
                    label="rebind",
                ),
                tape_entries=(),
                plugin_states={},
            ),
        )

    checkpoint = await store.load_checkpoint("checkpoint-a")
    assert checkpoint is not None
    assert checkpoint.meta.session_id == "session-a"
    assert checkpoint.meta.tape_id == "tape-a"


@pytest.mark.asyncio
async def test_sqlite_local_durable_store_restores_checkpoint_state_atomically(
    tmp_path: Path,
) -> None:
    store = SQLiteLocalDurableStore(tmp_path / "local.sqlite3")
    owner = await store.acquire_owner("session-a", "owner-a")
    created_at = datetime.now(UTC)
    await store.save_session(
        owner,
        {"id": "session-a", "session_id": "session-a", "tape_id": "tape-a"},
    )
    await store.append_tape_entries(
        owner,
        "tape-a",
        [
            {"kind": "message", "payload": {"text": "keep"}},
            {"kind": "message", "payload": {"text": "drop"}},
        ],
    )
    await store.save_checkpoint(
        owner,
        CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id="checkpoint-keep",
                tape_id="tape-a",
                session_id="session-a",
                entry_count=1,
                window_start=0,
                created_at=created_at,
                label="keep",
            ),
            tape_entries=({"kind": "message", "payload": {"text": "keep"}},),
            plugin_states={},
        ),
    )
    await store.save_checkpoint(
        owner,
        CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id="checkpoint-drop",
                tape_id="tape-a",
                session_id="session-a",
                entry_count=2,
                window_start=0,
                created_at=created_at + timedelta(seconds=1),
                label="drop",
            ),
            tape_entries=(
                {"kind": "message", "payload": {"text": "keep"}},
                {"kind": "message", "payload": {"text": "drop"}},
            ),
            plugin_states={},
        ),
    )
    await store.create_topic(
        owner,
        TopicRecord(
            topic_id="topic-keep",
            tape_id="tape-a",
            session_id="session-a",
            kind="conversation",
            status="open",
            title="keep",
            summary=None,
            owner=None,
            topic_initial_seq=0,
            topic_finalized_seq=None,
            created_at=created_at - timedelta(milliseconds=3),
            metadata={},
        ),
    )
    await store.finalize_topic(
        owner,
        "topic-keep",
        summary="valid before checkpoint",
        topic_finalized_seq=0,
        finalized_at=created_at,
        metadata={},
    )
    await store.create_topic(
        owner,
        TopicRecord(
            topic_id="topic-peer",
            tape_id="tape-a",
            session_id="session-a",
            kind="conversation",
            status="open",
            title="peer",
            summary=None,
            owner=None,
            topic_initial_seq=0,
            topic_finalized_seq=None,
            created_at=created_at - timedelta(milliseconds=2),
            metadata={},
        ),
    )
    await store.finalize_topic(
        owner,
        "topic-peer",
        summary="valid peer",
        topic_finalized_seq=0,
        finalized_at=created_at,
        metadata={},
    )
    await store.create_topic(
        owner,
        TopicRecord(
            topic_id="topic-reopen",
            tape_id="tape-a",
            session_id="session-a",
            kind="conversation",
            status="open",
            title="reopen",
            summary=None,
            owner=None,
            topic_initial_seq=0,
            topic_finalized_seq=None,
            created_at=created_at - timedelta(milliseconds=1),
            metadata={},
        ),
    )
    await store.finalize_topic(
        owner,
        "topic-reopen",
        summary="closed after checkpoint",
        topic_finalized_seq=1,
        finalized_at=created_at + timedelta(seconds=1),
        metadata={"after": True},
    )
    await store.create_topic(
        owner,
        TopicRecord(
            topic_id="topic-stale-finalized",
            tape_id="tape-a",
            session_id="session-a",
            kind="conversation",
            status="open",
            title="stale finalized",
            summary=None,
            owner=None,
            topic_initial_seq=0,
            topic_finalized_seq=None,
            created_at=created_at + timedelta(milliseconds=2),
            metadata={},
        ),
    )
    await store.finalize_topic(
        owner,
        "topic-stale-finalized",
        summary="finalized after checkpoint",
        topic_finalized_seq=1,
        finalized_at=created_at,
        metadata={},
    )
    await store.record_topic_anchor(
        owner,
        TopicAnchorRecord(
            topic_id="topic-keep",
            tape_id="tape-a",
            seq=0,
            anchor_type="start",
            entry_id="keep-anchor",
        ),
    )
    await store.record_topic_anchor(
        owner,
        TopicAnchorRecord(
            topic_id="topic-keep",
            tape_id="tape-a",
            seq=1,
            anchor_type="future",
            entry_id="future-anchor",
        ),
    )
    await store.record_recall_link(
        owner,
        TopicRecallLinkRecord(
            source_topic_id="topic-keep",
            recalled_topic_id="topic-peer",
            relation="mentions",
            anchor_seq=1,
            source_entry_start_seq=0,
            source_entry_end_seq=1,
        ),
    )
    await store.update_topic_cost(
        owner,
        TopicCostRecord(topic_id="topic-keep", total_tokens=10, run_count=1),
    )
    await store.create_topic(
        owner,
        TopicRecord(
            topic_id="topic-future",
            tape_id="tape-a",
            session_id="session-a",
            kind="conversation",
            status="open",
            title="future",
            summary=None,
            owner=None,
            topic_initial_seq=1,
            topic_finalized_seq=None,
            created_at=created_at + timedelta(milliseconds=3),
            metadata={},
        ),
    )
    await store.record_topic_anchor(
        owner,
        TopicAnchorRecord(
            topic_id="topic-future",
            tape_id="tape-a",
            seq=1,
            anchor_type="start",
            entry_id="future-topic-anchor",
        ),
    )
    await store.record_recall_link(
        owner,
        TopicRecallLinkRecord(
            source_topic_id="topic-future",
            recalled_topic_id="topic-keep",
            relation="mentions",
            anchor_seq=1,
            source_entry_start_seq=1,
            source_entry_end_seq=1,
        ),
    )
    await store.update_topic_cost(
        owner,
        TopicCostRecord(topic_id="topic-future", total_tokens=20, run_count=1),
    )
    await store.truncate_tape(owner, "tape-a", 0)
    await store.append_tape_entries(
        owner,
        "tape-a",
        [
            {"kind": "message", "payload": {"text": "wrong-prefix"}},
            {"kind": "message", "payload": {"text": "drop"}},
        ],
    )

    await store.restore_checkpoint_state(
        owner,
        CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id="checkpoint-keep",
                tape_id="tape-a",
                session_id="session-a",
                entry_count=1,
                window_start=0,
                created_at=created_at,
                label="keep",
            ),
            tape_entries=({"kind": "message", "payload": {"text": "keep"}},),
            plugin_states={},
        ),
        {
            "id": "session-a",
            "session_id": "session-a",
            "tape_id": "tape-a",
            "provider_name": "restored-provider",
        },
    )

    assert await store.load_tape("tape-a") == [
        {"kind": "message", "payload": {"text": "keep"}}
    ]
    assert store.load_session("session-a") == {
        "id": "session-a",
        "session_id": "session-a",
        "tape_id": "tape-a",
        "provider_name": "restored-provider",
    }
    assert await store.load_checkpoint("checkpoint-keep") is not None
    assert await store.load_checkpoint("checkpoint-drop") is None
    topic_reader = SQLiteTopicStore(tmp_path / "local.sqlite3")
    restored_topics = await topic_reader.list_topics(tape_id="tape-a")
    topics_by_id = {topic.topic_id: topic for topic in restored_topics}
    assert set(topics_by_id) == {"topic-keep", "topic-peer", "topic-reopen"}
    assert topics_by_id["topic-reopen"].status == "open"
    assert topics_by_id["topic-reopen"].summary is None
    assert topics_by_id["topic-reopen"].topic_finalized_seq is None
    assert topics_by_id["topic-reopen"].finalized_at is None
    assert topics_by_id["topic-reopen"].metadata == {}
    assert [
        (anchor.seq, anchor.anchor_type)
        for anchor in await topic_reader.list_topic_anchors("topic-keep")
    ] == [(0, "start")]
    assert await topic_reader.list_topic_anchors("topic-future") == []
    assert await topic_reader.list_recall_links("topic-keep") == []
    assert await topic_reader.list_recall_links("topic-future") == []
    assert await topic_reader.load_topic_cost("topic-keep") is None
    assert await topic_reader.load_topic_cost("topic-future") is None
    assert await topic_reader.list_topic_anchors("topic-stale-finalized") == []
