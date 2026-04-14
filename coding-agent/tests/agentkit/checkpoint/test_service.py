from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import agentkit.storage.checkpoint_fs as checkpoint_fs_module

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


@dataclass
class RecordingCheckpointStore:
    saved_snapshot: CheckpointSnapshot | None = None

    async def save(self, snapshot: CheckpointSnapshot) -> None:
        self.saved_snapshot = snapshot

    async def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        return None

    async def list_by_tape(self, tape_id: str) -> list[CheckpointMeta]:
        return []

    async def delete(self, checkpoint_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_capture_serializes_tape_entries_and_json_safe_plugin_states() -> None:
    from agentkit.checkpoint.service import CheckpointService

    tape = Tape(tape_id="stable-tape-id")
    tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
    ctx = PipelineContext(
        tape=tape,
        session_id="session-1",
        plugin_states={
            "safe": {"count": 1, "tags": ["a", "b"]},
            "unsafe": object(),
        },
    )
    store = RecordingCheckpointStore()

    meta = await CheckpointService(store).capture(
        ctx,
        label="before-change",
        extra={"workspace": "/tmp/repo"},
    )

    snapshot = store.saved_snapshot
    assert snapshot is not None
    assert meta == snapshot.meta
    assert snapshot.meta.tape_id == "stable-tape-id"
    assert snapshot.meta.session_id == "session-1"
    assert snapshot.meta.entry_count == 1
    assert snapshot.meta.label == "before-change"
    assert snapshot.tape_entries == (
        {
            "id": tape[0].id,
            "kind": "message",
            "payload": {"role": "user", "content": "hi"},
            "timestamp": tape[0].timestamp,
        },
    )
    assert snapshot.plugin_states == {"safe": {"count": 1, "tags": ["a", "b"]}}
    assert snapshot.extra == {"workspace": "/tmp/repo"}


@pytest.mark.asyncio
async def test_capture_rejects_non_json_safe_extra() -> None:
    from agentkit.checkpoint.service import CheckpointService

    ctx = PipelineContext(tape=Tape(), session_id="session-1")
    store = RecordingCheckpointStore()

    with pytest.raises(TypeError, match="extra must be JSON-serializable"):
        await CheckpointService(store).capture(
            ctx,
            extra={"bad": object()},
        )


@pytest.mark.asyncio
async def test_checkpoint_store_lists_meta_without_loading_entries(tmp_path) -> None:
    from agentkit.storage.checkpoint_fs import FSCheckpointStore

    store = FSCheckpointStore(tmp_path)
    first_meta = CheckpointMeta(
        checkpoint_id="cp-1",
        tape_id="tape-1",
        session_id="session-1",
        entry_count=2,
        window_start=0,
        created_at=datetime.now(UTC),
        label="first",
    )
    second_meta = CheckpointMeta(
        checkpoint_id="cp-2",
        tape_id="tape-2",
        session_id="session-2",
        entry_count=1,
        window_start=0,
        created_at=datetime.now(UTC),
        label="second",
    )
    await store.save(
        CheckpointSnapshot(
            meta=first_meta,
            tape_entries=(
                {
                    "id": "e-1",
                    "kind": "message",
                    "payload": {"content": "a"},
                    "timestamp": 1.0,
                },
                {
                    "id": "e-2",
                    "kind": "message",
                    "payload": {"content": "b"},
                    "timestamp": 2.0,
                },
            ),
            plugin_states={"safe": True},
            extra={"x": 1},
        )
    )
    await store.save(
        CheckpointSnapshot(
            meta=second_meta,
            tape_entries=(
                {
                    "id": "e-3",
                    "kind": "message",
                    "payload": {"content": "c"},
                    "timestamp": 3.0,
                },
            ),
            plugin_states={},
            extra={},
        )
    )

    listed = await store.list_by_tape("tape-1")

    assert listed == [first_meta]


@pytest.mark.asyncio
async def test_checkpoint_store_round_trips_full_snapshot(tmp_path) -> None:
    from agentkit.storage.checkpoint_fs import FSCheckpointStore

    store = FSCheckpointStore(tmp_path)
    snapshot = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="cp-roundtrip",
            tape_id="tape-roundtrip",
            session_id="session-roundtrip",
            entry_count=2,
            window_start=1,
            created_at=datetime.now(UTC),
            label="roundtrip",
        ),
        tape_entries=(
            {
                "id": "e-1",
                "kind": "message",
                "payload": {"role": "user", "content": "hello"},
                "timestamp": 1.0,
            },
            {
                "id": "e-2",
                "kind": "message",
                "payload": {"role": "assistant", "content": "world"},
                "timestamp": 2.0,
            },
        ),
        plugin_states={"topic": {"current_topic_id": "topic-1"}},
        extra={"workspace": "/tmp/repo"},
    )

    await store.save(snapshot)

    loaded = await store.load("cp-roundtrip")

    assert loaded == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkpoint_id",
    ["", "../evil", "nested/path", "/absolute", r"..\\evil"],
)
async def test_checkpoint_store_rejects_unsafe_checkpoint_ids(
    tmp_path: Path, checkpoint_id: str
) -> None:
    from agentkit.storage.checkpoint_fs import FSCheckpointStore

    store = FSCheckpointStore(tmp_path)
    snapshot = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id=checkpoint_id,
            tape_id="tape-roundtrip",
            session_id="session-roundtrip",
            entry_count=1,
            window_start=0,
            created_at=datetime.now(UTC),
            label="unsafe",
        ),
        tape_entries=(
            {
                "id": "e-1",
                "kind": "message",
                "payload": {"role": "user", "content": "hello"},
                "timestamp": 1.0,
            },
        ),
        plugin_states={},
        extra={},
    )

    with pytest.raises(ValueError, match="checkpoint_id"):
        await store.save(snapshot)

    with pytest.raises(ValueError, match="checkpoint_id"):
        await store.load(checkpoint_id)

    with pytest.raises(ValueError, match="checkpoint_id"):
        await store.delete(checkpoint_id)


@pytest.mark.asyncio
async def test_checkpoint_store_save_keeps_checkpoint_invisible_when_meta_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentkit.storage.checkpoint_fs import FSCheckpointStore

    store = FSCheckpointStore(tmp_path)
    snapshot = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="cp-atomic",
            tape_id="tape-atomic",
            session_id="session-atomic",
            entry_count=1,
            window_start=0,
            created_at=datetime.now(UTC),
            label="atomic",
        ),
        tape_entries=(
            {
                "id": "e-1",
                "kind": "message",
                "payload": {"role": "user", "content": "hello"},
                "timestamp": 1.0,
            },
        ),
        plugin_states={"topic": {"current_topic_id": "topic-1"}},
        extra={"workspace": "/tmp/repo"},
    )

    original_replace = checkpoint_fs_module.os.replace

    def fail_meta_replace(src: str | Path, dst: str | Path) -> None:
        if Path(dst).name.endswith(".meta.json"):
            raise OSError("meta replace failed")
        original_replace(src, dst)

    monkeypatch.setattr(checkpoint_fs_module.os, "replace", fail_meta_replace)

    with pytest.raises(OSError, match="meta replace failed"):
        await store.save(snapshot)

    assert await store.load("cp-atomic") is None
    assert await store.list_by_tape("tape-atomic") == []
    assert not tmp_path.joinpath("cp-atomic.entries.jsonl").exists()
    assert not tmp_path.joinpath("cp-atomic.state.json").exists()
    assert not tmp_path.joinpath("cp-atomic.meta.json").exists()
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_checkpoint_store_save_fsyncs_parent_directory_after_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentkit.storage.checkpoint_fs import FSCheckpointStore

    store = FSCheckpointStore(tmp_path)
    snapshot = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="cp-fsync",
            tape_id="tape-fsync",
            session_id="session-fsync",
            entry_count=1,
            window_start=0,
            created_at=datetime.now(UTC),
            label="fsync",
        ),
        tape_entries=(
            {
                "id": "e-1",
                "kind": "message",
                "payload": {"role": "user", "content": "hello"},
                "timestamp": 1.0,
            },
        ),
        plugin_states={},
        extra={},
    )

    fsync_targets: list[int] = []
    original_fsync = checkpoint_fs_module.os.fsync

    def record_fsync(fd: int) -> None:
        fsync_targets.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(checkpoint_fs_module.os, "fsync", record_fsync)

    await store.save(snapshot)

    assert len(fsync_targets) >= 4
