from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.protocols import CheckpointStore, TapeDebugStore, TapeStore
from agentkit.storage.sqlite import SQLiteCheckpointStore, SQLiteTapeStore


class TestSQLiteTapeStore:
    @pytest.fixture
    def store(self, tmp_path) -> SQLiteTapeStore:
        return SQLiteTapeStore(tmp_path / "tape.sqlite3")

    def test_satisfies_protocol(self, store: SQLiteTapeStore) -> None:
        assert isinstance(store, TapeStore)
        assert isinstance(store, TapeDebugStore)

    @pytest.mark.asyncio
    async def test_save_load_and_reopen_appends_in_sequence(
        self, store: SQLiteTapeStore, tmp_path
    ) -> None:
        await store.save(
            "tape-1",
            [{"kind": "message", "payload": {"role": "user", "content": "a"}}],
        )
        await store.save(
            "tape-1",
            [{"kind": "message", "payload": {"role": "assistant", "content": "b"}}],
        )

        reopened = SQLiteTapeStore(tmp_path / "tape.sqlite3")
        rows = await reopened.load("tape-1")

        assert [cast(dict[str, str], row["payload"])["content"] for row in rows] == [
            "a",
            "b",
        ]
        info = await reopened.info("tape-1")
        assert info is not None
        assert info.entry_count == 2
        assert info.first_seq == 0
        assert info.last_seq == 1

    @pytest.mark.asyncio
    async def test_list_ids_and_truncate(self, store: SQLiteTapeStore) -> None:
        await store.save(
            "tape-a",
            [
                {"kind": "message", "payload": {"content": "a"}},
                {"kind": "message", "payload": {"content": "b"}},
            ],
        )
        await store.save("tape-b", [{"kind": "message", "payload": {"content": "c"}}])

        await store.truncate("tape-a", 1)

        assert await store.list_ids() == ["tape-a", "tape-b"]
        assert [row["payload"] for row in await store.load("tape-a")] == [
            {"content": "a"}
        ]

    @pytest.mark.asyncio
    async def test_truncate_rejects_negative_keep(self, store: SQLiteTapeStore) -> None:
        with pytest.raises(ValueError, match="keep must be >= 0"):
            await store.truncate("tape-a", -1)

    @pytest.mark.asyncio
    async def test_search_filters_by_indexed_entry_metadata(
        self, store: SQLiteTapeStore
    ) -> None:
        await store.save(
            "tape-debug",
            [
                {
                    "kind": "message",
                    "payload": {"content": "hello", "run_id": "run-1"},
                    "meta": {"run_id": "run-1"},
                },
                {
                    "kind": "tool_call",
                    "payload": {"tool_call_id": "tool-1", "run_id": "run-1"},
                },
                {
                    "kind": "tool_result",
                    "payload": {"tool_call_id": "tool-1"},
                    "meta": {"run_id": "run-1"},
                },
                {
                    "id": "anchor-1",
                    "kind": "anchor",
                    "payload": {"summary": "folded"},
                    "anchor_type": "resume_boundary",
                    "meta": {"run_id": "run-2"},
                },
            ],
        )
        await store.save(
            "tape-other",
            [{"kind": "tool_call", "payload": {"tool_call_id": "tool-2"}}],
        )

        kind_results = await store.search(kind="tool_call")
        call_results = await store.search(
            tape_id="tape-debug",
            run_id="run-1",
            tool_call_id="tool-1",
        )
        anchor_results = await store.search(anchor_type="resume_boundary")

        assert [(item.tape_id, item.seq) for item in kind_results] == [
            ("tape-debug", 1),
            ("tape-other", 0),
        ]
        assert [item.entry["kind"] for item in call_results] == [
            "tool_call",
            "tool_result",
        ]
        assert [(item.tape_id, item.entry["kind"]) for item in anchor_results] == [
            ("tape-debug", "anchor")
        ]

    @pytest.mark.asyncio
    async def test_search_rejects_non_positive_limit(
        self, store: SQLiteTapeStore
    ) -> None:
        with pytest.raises(ValueError, match="limit must be positive"):
            await store.search(limit=0)

    def test_memory_records_share_sqlite_tape_storage(
        self, store: SQLiteTapeStore
    ) -> None:
        store.append_memory_record("session-1", {"summary": "first"})
        store.append_memory_record("session-1", {"summary": "second"})

        assert store.load_memory_records("session-1") == [
            {"summary": "first"},
            {"summary": "second"},
        ]

        store.replace_memory_records("session-1", [{"summary": "replacement"}])

        assert store.load_memory_records("session-1") == [{"summary": "replacement"}]

    @pytest.mark.asyncio
    async def test_memory_records_are_visible_as_tape_entries(
        self, store: SQLiteTapeStore
    ) -> None:
        store.append_memory_record("session-1", {"summary": "persisted"})

        entries = await store.load("session-1")

        assert [entry["kind"] for entry in entries] == ["memory_record"]
        assert entries[0]["payload"] == {"summary": "persisted"}


class TestSQLiteCheckpointStore:
    @pytest.fixture
    def store(self, tmp_path) -> SQLiteCheckpointStore:
        return SQLiteCheckpointStore(tmp_path / "checkpoints.sqlite3")

    def _snapshot(
        self, checkpoint_id: str, tape_id: str, *, created_at: datetime
    ) -> CheckpointSnapshot:
        return CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id=checkpoint_id,
                tape_id=tape_id,
                session_id="session-1",
                entry_count=2,
                window_start=1,
                created_at=created_at,
                label=checkpoint_id,
            ),
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
            plugin_states={"topic": {"current": checkpoint_id}},
            extra={"source": checkpoint_id},
        )

    def test_satisfies_protocol(self, store: SQLiteCheckpointStore) -> None:
        assert isinstance(store, CheckpointStore)

    @pytest.mark.asyncio
    async def test_round_trip_snapshot_across_instances(
        self, store: SQLiteCheckpointStore, tmp_path
    ) -> None:
        snapshot = self._snapshot(
            "cp-roundtrip",
            "tape-roundtrip",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
        )

        await store.save(snapshot)
        reopened = SQLiteCheckpointStore(tmp_path / "checkpoints.sqlite3")

        assert await reopened.load("cp-roundtrip") == snapshot

    @pytest.mark.asyncio
    async def test_overwrite_list_and_delete(
        self, store: SQLiteCheckpointStore
    ) -> None:
        first = self._snapshot(
            "cp-retry",
            "tape-a",
            created_at=datetime(2026, 6, 1, 1, tzinfo=UTC),
        )
        replacement = self._snapshot(
            "cp-retry",
            "tape-a",
            created_at=datetime(2026, 6, 1, 2, tzinfo=UTC),
        )
        earlier = self._snapshot(
            "cp-earlier",
            "tape-a",
            created_at=datetime(2026, 6, 1, 0, tzinfo=UTC),
        )
        other = self._snapshot(
            "cp-other",
            "tape-b",
            created_at=datetime(2026, 6, 1, 3, tzinfo=UTC),
        )

        await store.save(first)
        await store.save(replacement)
        await store.save(earlier)
        await store.save(other)

        listed = await store.list_by_tape("tape-a")
        assert listed == [earlier.meta, replacement.meta]
        assert await store.load("cp-retry") == replacement

        await store.delete("cp-retry")

        assert await store.load("cp-retry") is None
