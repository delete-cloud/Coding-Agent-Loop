from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

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
