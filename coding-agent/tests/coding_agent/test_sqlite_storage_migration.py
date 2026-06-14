from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.checkpoint_fs import FSCheckpointStore
from agentkit.storage.sqlite import SQLiteCheckpointStore, SQLiteTapeStore
from coding_agent.stores.migration import (
    migrate_fs_checkpoints_to_sqlite,
    migrate_jsonl_tapes_to_sqlite,
    migrate_legacy_storage_to_sqlite,
)


@pytest.mark.asyncio
async def test_migrate_jsonl_tapes_to_sqlite_is_idempotent(tmp_path: Path) -> None:
    tapes_dir = tmp_path / "tapes"
    tapes_dir.mkdir()
    (tapes_dir / "tape-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"kind": "message", "payload": {"content": "one"}}),
                json.dumps({"kind": "message", "payload": {"content": "two"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    sqlite_path = tmp_path / "tape.sqlite3"

    first = await migrate_jsonl_tapes_to_sqlite(tapes_dir, sqlite_path)
    second = await migrate_jsonl_tapes_to_sqlite(tapes_dir, sqlite_path)

    store = SQLiteTapeStore(sqlite_path)
    assert first.scanned == 1
    assert first.migrated == 1
    assert first.skipped == 0
    assert second.scanned == 1
    assert second.migrated == 0
    assert second.skipped == 1
    assert await store.load("tape-1") == [
        {"kind": "message", "payload": {"content": "one"}},
        {"kind": "message", "payload": {"content": "two"}},
    ]


@pytest.mark.asyncio
async def test_migrate_jsonl_tapes_fails_on_existing_mismatch(
    tmp_path: Path,
) -> None:
    tapes_dir = tmp_path / "tapes"
    tapes_dir.mkdir()
    (tapes_dir / "tape-1.jsonl").write_text(
        json.dumps({"kind": "message", "payload": {"content": "source"}}) + "\n",
        encoding="utf-8",
    )
    sqlite_path = tmp_path / "tape.sqlite3"
    store = SQLiteTapeStore(sqlite_path)
    await store.save(
        "tape-1",
        [{"kind": "message", "payload": {"content": "target"}}],
    )

    with pytest.raises(ValueError, match="already exists with different entries"):
        await migrate_jsonl_tapes_to_sqlite(tapes_dir, sqlite_path)


@pytest.mark.asyncio
async def test_migrate_jsonl_tapes_dry_run_does_not_create_sqlite(
    tmp_path: Path,
) -> None:
    tapes_dir = tmp_path / "tapes"
    tapes_dir.mkdir()
    (tapes_dir / "tape-1.jsonl").write_text(
        json.dumps({"kind": "message", "payload": {"content": "source"}}) + "\n",
        encoding="utf-8",
    )
    sqlite_path = tmp_path / "tape.sqlite3"

    report = await migrate_jsonl_tapes_to_sqlite(tapes_dir, sqlite_path, dry_run=True)

    assert report.scanned == 1
    assert report.migrated == 1
    assert not sqlite_path.exists()


@pytest.mark.asyncio
async def test_migrate_jsonl_tapes_skips_empty_files(tmp_path: Path) -> None:
    tapes_dir = tmp_path / "tapes"
    tapes_dir.mkdir()
    (tapes_dir / "empty-tape.jsonl").write_text("", encoding="utf-8")
    sqlite_path = tmp_path / "tape.sqlite3"

    first = await migrate_jsonl_tapes_to_sqlite(tapes_dir, sqlite_path)
    second = await migrate_jsonl_tapes_to_sqlite(tapes_dir, sqlite_path)

    assert first.scanned == 1
    assert first.migrated == 0
    assert first.skipped == 1
    assert second.scanned == 1
    assert second.migrated == 0
    assert second.skipped == 1


@pytest.mark.asyncio
async def test_migrate_fs_checkpoints_to_sqlite_round_trips_snapshot(
    tmp_path: Path,
) -> None:
    checkpoints_dir = tmp_path / "checkpoints"
    fs_store = FSCheckpointStore(checkpoints_dir)
    snapshot = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="cp-1",
            tape_id="tape-1",
            session_id="session-1",
            entry_count=1,
            window_start=0,
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
            label="before-edit",
        ),
        tape_entries=(
            {
                "id": "entry-1",
                "kind": "message",
                "payload": {"content": "saved"},
                "timestamp": 1.0,
            },
        ),
        plugin_states={"topic": {"id": "topic-1"}},
        extra={"repo": "/workspace"},
    )
    await fs_store.save(snapshot)

    sqlite_path = tmp_path / "checkpoints.sqlite3"

    report = await migrate_fs_checkpoints_to_sqlite(checkpoints_dir, sqlite_path)

    sqlite_store = SQLiteCheckpointStore(sqlite_path)
    assert report.scanned == 1
    assert report.migrated == 1
    assert await sqlite_store.load("cp-1") == snapshot


@pytest.mark.asyncio
async def test_migrate_fs_checkpoints_dry_run_does_not_create_sqlite(
    tmp_path: Path,
) -> None:
    checkpoints_dir = tmp_path / "checkpoints"
    fs_store = FSCheckpointStore(checkpoints_dir)
    await fs_store.save(
        CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id="cp-1",
                tape_id="tape-1",
                session_id="session-1",
                entry_count=1,
                window_start=0,
                created_at=datetime(2026, 6, 1, tzinfo=UTC),
                label=None,
            ),
            tape_entries=(
                {
                    "id": "entry-1",
                    "kind": "message",
                    "payload": {"content": "saved"},
                    "timestamp": 1.0,
                },
            ),
            plugin_states={},
            extra={},
        )
    )
    sqlite_path = tmp_path / "checkpoints.sqlite3"

    report = await migrate_fs_checkpoints_to_sqlite(
        checkpoints_dir, sqlite_path, dry_run=True
    )

    assert report.scanned == 1
    assert report.migrated == 1
    assert not sqlite_path.exists()


@pytest.mark.asyncio
async def test_migrate_legacy_storage_to_sqlite_uses_default_paths(
    tmp_path: Path,
) -> None:
    tapes_dir = tmp_path / "tapes"
    tapes_dir.mkdir()
    (tapes_dir / "tape-1.jsonl").write_text(
        json.dumps({"kind": "message", "payload": {"content": "source"}}) + "\n",
        encoding="utf-8",
    )
    fs_store = FSCheckpointStore(tmp_path / "checkpoints")
    await fs_store.save(
        CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id="cp-1",
                tape_id="tape-1",
                session_id="session-1",
                entry_count=1,
                window_start=0,
                created_at=datetime(2026, 6, 1, tzinfo=UTC),
                label=None,
            ),
            tape_entries=(
                {
                    "id": "entry-1",
                    "kind": "message",
                    "payload": {"content": "source"},
                    "timestamp": 1.0,
                },
            ),
            plugin_states={},
            extra={},
        )
    )

    report = await migrate_legacy_storage_to_sqlite(tmp_path)

    assert report.tapes.scanned == 1
    assert report.tapes.migrated == 1
    assert report.checkpoints.scanned == 1
    assert report.checkpoints.migrated == 1
    local_sqlite = tmp_path / "local.sqlite3"
    assert await SQLiteTapeStore(local_sqlite).load("tape-1") == [
        {"kind": "message", "payload": {"content": "source"}}
    ]
    assert await SQLiteCheckpointStore(local_sqlite).load("cp-1") is not None
