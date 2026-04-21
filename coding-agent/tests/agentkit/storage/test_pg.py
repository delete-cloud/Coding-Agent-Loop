import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest
from _pytest.monkeypatch import MonkeyPatch

from datetime import UTC, datetime

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.storage.protocols import CheckpointStore, SessionStore, TapeStore
from agentkit.storage.pg import (
    PGCheckpointStore,
    PGPool,
    PGSessionLock,
    PGSessionOwnerStore,
    PGSessionStore,
    PGTapeStore,
)


class FakePool:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.session_owners: dict[str, dict[str, object]] = {}
        self.tapes: dict[str, list[dict[str, int | dict[str, object]]]] = {}
        self.checkpoints: dict[str, dict[str, object]] = {}
        self.closed: bool = False
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        if "INSERT INTO agent_sessions" in query:
            session_id, payload = args
            if not isinstance(session_id, str):
                raise TypeError("session_id must be a string")
            if isinstance(payload, str):
                payload_obj = json.loads(payload)
            elif isinstance(payload, dict):
                payload_obj = payload
            else:
                raise TypeError("payload must be encoded json or dict")
            if not isinstance(payload_obj, dict):
                raise TypeError("payload must decode to a dict")
            self.sessions[session_id] = cast(dict[str, object], payload_obj)
            return "INSERT 0 1"
        if "DELETE FROM agent_sessions" in query:
            (session_id,) = args
            if not isinstance(session_id, str):
                raise TypeError("session_id must be a string")
            _ = self.sessions.pop(session_id, None)
            return "DELETE 1"
        if "INSERT INTO agent_tapes" in query:
            tape_id, payload_values = args
            if not isinstance(tape_id, str):
                raise TypeError("tape_id must be a string")
            rows = self.tapes.setdefault(tape_id, [])
            if not (
                isinstance(payload_values, list)
                and all(isinstance(item, (str, dict)) for item in payload_values)
            ):
                raise TypeError("payload must be encoded json/dict or list thereof")
            max_seq = max(
                (row["seq"] for row in rows if isinstance(row["seq"], int)), default=-1
            )
            for offset, payload in enumerate(payload_values, start=1):
                seq = max_seq + offset
                payload_obj = (
                    json.loads(payload) if isinstance(payload, str) else payload
                )
                if not isinstance(payload_obj, dict):
                    raise TypeError("payload must decode to a dict")
                rows.append({"seq": seq, "entry": cast(dict[str, object], payload_obj)})
            rows.sort(
                key=lambda item: item["seq"] if isinstance(item["seq"], int) else -1
            )
            return "INSERT 0 1"
        if "INSERT INTO agent_checkpoints" in query:
            checkpoint_id, tape_id, meta_json, entries_json, state_json, extra_json = (
                args
            )
            if not isinstance(checkpoint_id, str):
                raise TypeError("checkpoint_id must be a string")
            if not isinstance(tape_id, str):
                raise TypeError("tape_id must be a string")
            if not all(
                isinstance(item, (str, dict, list))
                for item in (meta_json, entries_json, state_json, extra_json)
            ):
                raise TypeError("checkpoint payloads must be json-compatible values")
            self.checkpoints[checkpoint_id] = {
                "checkpoint_id": checkpoint_id,
                "tape_id": tape_id,
                "meta": json.loads(meta_json)
                if isinstance(meta_json, str)
                else meta_json,
                "entries": json.loads(entries_json)
                if isinstance(entries_json, str)
                else entries_json,
                "plugin_states": json.loads(state_json)
                if isinstance(state_json, str)
                else state_json,
                "extra": json.loads(extra_json)
                if isinstance(extra_json, str)
                else extra_json,
            }
            return "INSERT 0 1"
        if "INSERT INTO session_owners" in query:
            session_id, owner_id, lease_expires_at, fencing_token = args
            if not isinstance(session_id, str):
                raise TypeError("session_id must be a string")
            owner = self.session_owners.get(session_id)
            now = datetime.now(UTC)
            if (
                owner is not None
                and isinstance(owner.get("lease_expires_at"), datetime)
                and cast(datetime, owner["lease_expires_at"]) > now
            ):
                return "INSERT 0 0"
            if not isinstance(lease_expires_at, (int, float)):
                raise TypeError("lease_expires_at must be numeric lease seconds")
            self.session_owners[session_id] = {
                "owner_id": owner_id,
                "lease_expires_at": now + timedelta(seconds=float(lease_expires_at)),
                "fencing_token": fencing_token,
            }
            return "INSERT 0 1"
        if "UPDATE session_owners" in query:
            (
                lease_expires_at,
                new_fencing_token,
                session_id,
                owner_id,
                current_fencing_token,
            ) = args
            owner = self.session_owners.get(cast(str, session_id))
            if owner is None:
                return "UPDATE 0"
            now = datetime.now(UTC)
            if not isinstance(owner.get("lease_expires_at"), datetime):
                raise TypeError("lease_expires_at must be a datetime")
            if cast(datetime, owner["lease_expires_at"]) <= now:
                return "UPDATE 0"
            if (
                owner["owner_id"] != owner_id
                or owner["fencing_token"] != current_fencing_token
            ):
                return "UPDATE 0"
            if not isinstance(lease_expires_at, (int, float)):
                raise TypeError("lease_expires_at must be numeric lease seconds")
            owner["lease_expires_at"] = now + timedelta(seconds=float(lease_expires_at))
            owner["fencing_token"] = new_fencing_token
            return "UPDATE 1"
        if "DELETE FROM session_owners" in query:
            session_id, owner_id, fencing_token = args
            owner = self.session_owners.get(cast(str, session_id))
            if owner is None:
                return "DELETE 0"
            if owner["owner_id"] != owner_id or owner["fencing_token"] != fencing_token:
                return "DELETE 0"
            del self.session_owners[cast(str, session_id)]
            return "DELETE 1"
        if query.strip() == "SELECT 1":
            return "SELECT 1"
        if "DELETE FROM agent_tapes WHERE tape_id = $1 AND seq >= $2" in query:
            tape_id, keep = args
            if not isinstance(tape_id, str):
                raise TypeError("tape_id must be a string")
            if not isinstance(keep, int):
                raise TypeError("keep must be an int")
            rows = self.tapes.get(tape_id, [])
            self.tapes[tape_id] = [
                row for row in rows if isinstance(row["seq"], int) and row["seq"] < keep
            ]
            return "DELETE 1"
        if "CREATE TABLE IF NOT EXISTS agent_sessions" in query:
            return "CREATE TABLE"
        if "CREATE TABLE IF NOT EXISTS agent_tapes" in query:
            return "CREATE TABLE"
        if "CREATE TABLE IF NOT EXISTS agent_checkpoints" in query:
            return "CREATE TABLE"
        if "CREATE TABLE IF NOT EXISTS session_owners" in query:
            return "CREATE TABLE"
        if "DELETE FROM agent_checkpoints" in query:
            (checkpoint_id,) = args
            if not isinstance(checkpoint_id, str):
                raise TypeError("checkpoint_id must be a string")
            _ = self.checkpoints.pop(checkpoint_id, None)
            return "DELETE 1"
        raise AssertionError(f"unexpected execute query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.executed.append((query, args))
        if "SELECT payload FROM agent_sessions" not in query:
            if (
                "SELECT owner_id, lease_expires_at, fencing_token FROM session_owners"
                in query
            ):
                (session_id,) = args
                if not isinstance(session_id, str):
                    raise TypeError("session_id must be a string")
                owner = self.session_owners.get(session_id)
                if owner is None:
                    return None
                lease_expires_at = owner.get("lease_expires_at")
                if not isinstance(lease_expires_at, datetime):
                    raise TypeError("lease_expires_at must be a datetime")
                if lease_expires_at <= datetime.now(UTC):
                    return None
                return owner
            if (
                "SELECT meta, entries, plugin_states, extra FROM agent_checkpoints"
                in query
            ):
                (checkpoint_id,) = args
                if not isinstance(checkpoint_id, str):
                    raise TypeError("checkpoint_id must be a string")
                payload = self.checkpoints.get(checkpoint_id)
                if payload is None:
                    return None
                return {
                    "meta": payload["meta"],
                    "entries": payload["entries"],
                    "plugin_states": payload["plugin_states"],
                    "extra": payload["extra"],
                }
            raise AssertionError(f"unexpected fetchrow query: {query}")
        (session_id,) = args
        if not isinstance(session_id, str):
            raise TypeError("session_id must be a string")
        payload = self.sessions.get(session_id)
        if payload is None:
            return None
        return {"payload": payload}

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.executed.append((query, args))
        if "SELECT session_id FROM agent_sessions" in query:
            return [
                {"session_id": session_id}
                for session_id in sorted(self.sessions.keys())
            ]
        if "SELECT entry FROM agent_tapes" in query:
            (tape_id,) = args
            if not isinstance(tape_id, str):
                raise TypeError("tape_id must be a string")
            rows = self.tapes.get(tape_id, [])
            return [
                {"entry": row["entry"]}
                for row in sorted(
                    rows,
                    key=lambda r: r["seq"] if isinstance(r["seq"], int) else -1,
                )
            ]
        if "SELECT DISTINCT tape_id FROM agent_tapes" in query:
            return [{"tape_id": tape_id} for tape_id in sorted(self.tapes.keys())]
        if "SELECT meta FROM agent_checkpoints" in query:
            (tape_id,) = args
            if not isinstance(tape_id, str):
                raise TypeError("tape_id must be a string")
            rows = [
                {"meta": payload["meta"]}
                for payload in self.checkpoints.values()
                if payload["tape_id"] == tape_id
            ]
            if "::timestamptz" in query:
                rows.sort(
                    key=lambda row: datetime.fromisoformat(
                        str(cast(dict[str, object], row["meta"])["created_at"])
                    )
                )
            else:
                rows.sort(
                    key=lambda row: str(
                        cast(dict[str, object], row["meta"])["created_at"]
                    )
                )
            return rows
        raise AssertionError(f"unexpected fetch query: {query}")

    async def close(self) -> None:
        self.closed = True

    async def acquire(self) -> "FakePool":
        return self

    async def release(self, connection: object) -> None:
        if connection is not self:
            raise AssertionError("unexpected connection released")


class FakeInitConnection:
    def __init__(self) -> None:
        self.codec_calls: list[dict[str, object]] = []

    async def set_type_codec(
        self,
        typename: str,
        *,
        encoder: object,
        decoder: object,
        schema: str,
    ) -> None:
        self.codec_calls.append(
            {
                "typename": typename,
                "encoder": encoder,
                "decoder": decoder,
                "schema": schema,
            }
        )


class TestPGPool:
    @pytest.mark.asyncio
    async def test_creates_pool_lazily_and_reuses_instance(self):
        created: list[FakePool] = []

        async def fake_pool_factory(**kwargs: object) -> FakePool:
            assert kwargs["dsn"] == "postgresql://example"
            assert kwargs["min_size"] == 2
            assert kwargs["max_size"] == 5
            pool = FakePool()
            created.append(pool)
            return pool

        pool = PGPool(
            dsn="postgresql://example",
            min_size=2,
            max_size=5,
            pool_factory=fake_pool_factory,
        )

        first = await pool.get_pool()
        second = await pool.get_pool()

        assert first is second
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_close_closes_underlying_pool(self):
        fake_pool = FakePool()

        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        pool = PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)
        await pool.get_pool()

        await pool.close()

        assert fake_pool.closed is True

    @pytest.mark.asyncio
    async def test_concurrent_get_pool_reuses_single_factory_result(self):
        created: list[FakePool] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_pool_factory(**_: object) -> FakePool:
            pool = FakePool()
            created.append(pool)
            started.set()
            await release.wait()
            return pool

        pool = PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

        first_task = asyncio.create_task(pool.get_pool())
        await started.wait()
        second_task = asyncio.create_task(pool.get_pool())
        release.set()

        first, second = await asyncio.gather(first_task, second_task)

        assert first is second
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_missing_asyncpg_raises_clear_error(self, monkeypatch: MonkeyPatch):
        def fake_import_module(name: str):
            if name == "asyncpg":
                raise ModuleNotFoundError(name)
            return __import__(name)

        monkeypatch.setattr("importlib.import_module", fake_import_module)

        pool = PGPool(dsn="postgresql://example")

        with pytest.raises(ImportError, match="asyncpg is required"):
            await pool.get_pool()

    @pytest.mark.asyncio
    async def test_registers_json_and_jsonb_codecs_on_pool_connections(self):
        seen_connections: list[FakeInitConnection] = []

        async def fake_pool_factory(**kwargs: object) -> FakePool:
            init = kwargs.get("init")
            if not callable(init):
                raise AssertionError("expected asyncpg init callback")
            conn = FakeInitConnection()
            seen_connections.append(conn)
            await cast(Callable[[FakeInitConnection], Awaitable[object]], init)(conn)
            return FakePool()

        pool = PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

        _ = await pool.get_pool()

        assert len(seen_connections) == 1
        codec_names = [
            cast(str, call["typename"]) for call in seen_connections[0].codec_calls
        ]
        assert codec_names == ["json", "jsonb"]
        assert all(
            call["schema"] == "pg_catalog" for call in seen_connections[0].codec_calls
        )

    @pytest.mark.asyncio
    async def test_acquire_delegates_to_underlying_pool(self):
        fake_pool = FakePool()

        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        pool = PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

        conn = await pool.acquire()

        assert conn is fake_pool

    @pytest.mark.asyncio
    async def test_release_delegates_to_underlying_pool(self):
        fake_pool = FakePool()

        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        pool = PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)
        await pool.get_pool()

        await pool.release(fake_pool)


@pytest.mark.asyncio
async def test_pg_checkpoint_store_orders_list_by_created_at_timestamp() -> None:
    async def fake_pool_factory(**_: object) -> FakePool:
        return fake_pool

    fake_pool = FakePool()
    store = PGCheckpointStore(
        pool=PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)
    )

    older = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="cp-older",
            tape_id="tape-1",
            session_id="session-1",
            entry_count=1,
            window_start=0,
            created_at=datetime.fromisoformat("2026-04-18T12:30:00+02:00"),
            label="older",
        ),
        tape_entries=tuple(),
        plugin_states={},
        extra={},
    )
    newer = CheckpointSnapshot(
        meta=CheckpointMeta(
            checkpoint_id="cp-newer",
            tape_id="tape-1",
            session_id="session-1",
            entry_count=2,
            window_start=0,
            created_at=datetime.fromisoformat("2026-04-18T11:45:00+00:00"),
            label="newer",
        ),
        tape_entries=tuple(),
        plugin_states={},
        extra={},
    )

    await store.save(newer)
    await store.save(older)

    metas = await store.list_by_tape("tape-1")

    assert [meta.checkpoint_id for meta in metas] == ["cp-older", "cp-newer"]
    list_queries = [
        query
        for query, _args in fake_pool.executed
        if "SELECT meta FROM agent_checkpoints" in query
    ]
    assert list_queries
    assert "::timestamptz" in list_queries[-1]


class TestPGSessionStore:
    @pytest.fixture
    def fake_pool(self) -> FakePool:
        return FakePool()

    @pytest.fixture
    def pool(self, fake_pool: FakePool) -> PGPool:
        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        return PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

    @pytest.fixture
    def store(self, pool: PGPool) -> PGSessionStore:
        return PGSessionStore(pool=pool)

    def test_satisfies_protocol(self, store: PGSessionStore):
        assert isinstance(store, SessionStore)

    @pytest.mark.asyncio
    async def test_save_and_load_session(self, store: PGSessionStore):
        await store.save_session("ses-1", {"model": "gpt-4.1", "turns": 3})

        data = await store.load_session("ses-1")

        assert data == {"model": "gpt-4.1", "turns": 3}

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self, store: PGSessionStore):
        assert await store.load_session("missing") is None

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self, store: PGSessionStore):
        await store.save_session("ses-1", {"version": 1})
        await store.save_session("ses-1", {"version": 2})

        data = await store.load_session("ses-1")

        assert data == {"version": 2}

    @pytest.mark.asyncio
    async def test_list_sessions_returns_sorted_ids(self, store: PGSessionStore):
        await store.save_session("b", {"x": 2})
        await store.save_session("a", {"x": 1})

        session_ids = await store.list_sessions()

        assert session_ids == ["a", "b"]

    @pytest.mark.asyncio
    async def test_delete_session(self, store: PGSessionStore):
        await store.save_session("ses-1", {"x": 1})

        await store.delete_session("ses-1")

        assert await store.load_session("ses-1") is None

    @pytest.mark.asyncio
    async def test_schema_created_once(
        self, store: PGSessionStore, fake_pool: FakePool
    ):
        await store.save_session("one", {"x": 1})
        await store.save_session("two", {"x": 2})

        schema_calls = [
            query
            for query, _ in fake_pool.executed
            if "CREATE TABLE IF NOT EXISTS agent_sessions" in query
        ]
        assert len(schema_calls) == 1

    @pytest.mark.asyncio
    async def test_save_session_passes_python_object_to_codec_enabled_pool(
        self, store: PGSessionStore, fake_pool: FakePool
    ):
        await store.save_session("ses-1", {"model": "gpt-4.1"})

        insert_calls = [
            args
            for query, args in fake_pool.executed
            if "INSERT INTO agent_sessions" in query
        ]
        assert len(insert_calls) == 1
        assert isinstance(insert_calls[0][1], dict)


class TestPGSessionOwnerStore:
    @pytest.fixture
    def fake_pool(self) -> FakePool:
        return FakePool()

    @pytest.fixture
    def pool(self, fake_pool: FakePool) -> PGPool:
        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        return PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

    @pytest.fixture
    def store(self, pool: PGPool) -> PGSessionOwnerStore:
        return PGSessionOwnerStore(pool=pool)

    @pytest.mark.asyncio
    async def test_acquire_returns_true_on_new_session(
        self, store: PGSessionOwnerStore
    ) -> None:
        assert await store.acquire("s1", "owner-a", 30.0, 1) is True

    @pytest.mark.asyncio
    async def test_acquire_returns_false_on_existing_live_lease(
        self, store: PGSessionOwnerStore
    ) -> None:
        await store.acquire("s1", "owner-a", 30.0, 1)

        assert await store.acquire("s1", "owner-b", 30.0, 2) is False

    @pytest.mark.asyncio
    async def test_renew_updates_fencing_token(
        self, store: PGSessionOwnerStore
    ) -> None:
        await store.acquire("s1", "owner-a", 30.0, 1)

        renewed = await store.renew("s1", "owner-a", 30.0, 2, 1)
        owner = await store.get_owner("s1")

        assert renewed is True
        assert owner is not None
        assert owner["fencing_token"] == 2

    @pytest.mark.asyncio
    async def test_renew_rejects_stale_fencing_token(
        self, store: PGSessionOwnerStore
    ) -> None:
        await store.acquire("s1", "owner-a", 30.0, 1)

        assert await store.renew("s1", "owner-a", 30.0, 2, 99) is False

    @pytest.mark.asyncio
    async def test_release_removes_owner(self, store: PGSessionOwnerStore) -> None:
        await store.acquire("s1", "owner-a", 30.0, 1)

        assert await store.release("s1", "owner-a", 1) is True
        assert await store.get_owner("s1") is None

    @pytest.mark.asyncio
    async def test_get_owner_filters_expired_leases(
        self, store: PGSessionOwnerStore, fake_pool: FakePool
    ) -> None:
        fake_pool.session_owners["s1"] = {
            "owner_id": "owner-a",
            "lease_expires_at": datetime.now(UTC) - timedelta(seconds=1),
            "fencing_token": 1,
        }

        assert await store.get_owner("s1") is None


class TestPGTapeStore:
    @pytest.fixture
    def fake_pool(self) -> FakePool:
        return FakePool()

    @pytest.fixture
    def pool(self, fake_pool: FakePool) -> PGPool:
        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        return PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

    @pytest.fixture
    def store(self, pool: PGPool) -> PGTapeStore:
        return PGTapeStore(pool=pool)

    def test_satisfies_protocol(self, store: PGTapeStore):
        assert isinstance(store, TapeStore)

    @pytest.mark.asyncio
    async def test_save_computes_seq_from_zero(self, store: PGTapeStore):
        await store.save(
            "tape-1",
            [{"kind": "message", "payload": {"role": "user", "content": "hi"}}],
        )

        rows = await store.load("tape-1")

        assert len(rows) == 1
        assert rows[0]["kind"] == "message"

    @pytest.mark.asyncio
    async def test_save_appends_after_existing(self, store: PGTapeStore):
        await store.save(
            "tape-1",
            [{"kind": "message", "payload": {"role": "user", "content": "a"}}],
        )
        await store.save(
            "tape-1",
            [{"kind": "message", "payload": {"role": "assistant", "content": "b"}}],
        )

        rows = await store.load("tape-1")

        assert len(rows) == 2
        first_payload = rows[0].get("payload")
        second_payload = rows[1].get("payload")
        assert isinstance(first_payload, dict)
        assert isinstance(second_payload, dict)
        assert first_payload["content"] == "a"
        assert second_payload["content"] == "b"

    @pytest.mark.asyncio
    async def test_save_empty_entries_is_noop(self, store: PGTapeStore):
        await store.save("tape-1", [])

        assert await store.load("tape-1") == []

    @pytest.mark.asyncio
    async def test_save_batches_insert_round_trip(
        self, store: PGTapeStore, fake_pool: FakePool
    ):
        await store.save(
            "tape-batch",
            [
                {"kind": "message", "payload": {"role": "user", "content": "a"}},
                {
                    "kind": "message",
                    "payload": {"role": "assistant", "content": "b"},
                },
                {"kind": "message", "payload": {"role": "user", "content": "c"}},
            ],
        )

        insert_calls = [
            (query, args)
            for query, args in fake_pool.executed
            if "INSERT INTO agent_tapes" in query
        ]

        assert len(insert_calls) == 1

    @pytest.mark.asyncio
    async def test_save_uses_single_atomic_append_statement(
        self, store: PGTapeStore, fake_pool: FakePool
    ):
        await store.save(
            "tape-atomic",
            [{"kind": "message", "payload": {"role": "user", "content": "x"}}],
        )

        tape_queries = [
            query for query, _args in fake_pool.executed if "agent_tapes" in query
        ]

        assert any("pg_advisory_xact_lock" in query for query in tape_queries)
        assert all("SELECT MAX(seq) AS max_seq" not in query for query in tape_queries)

    @pytest.mark.asyncio
    async def test_load_empty_tape(self, store: PGTapeStore):
        result = await store.load("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_ids(self, store: PGTapeStore):
        await store.save(
            "tape-a", [{"kind": "message", "payload": {"role": "user", "content": "a"}}]
        )
        await store.save(
            "tape-b", [{"kind": "message", "payload": {"role": "user", "content": "b"}}]
        )

        result = await store.list_ids()
        assert result == ["tape-a", "tape-b"]

    @pytest.mark.asyncio
    async def test_truncate_discards_entries_at_and_after_keep(
        self, store: PGTapeStore
    ):
        await store.save(
            "tape-truncate",
            [
                {"kind": "message", "payload": {"role": "user", "content": "a"}},
                {"kind": "message", "payload": {"role": "assistant", "content": "b"}},
                {"kind": "message", "payload": {"role": "user", "content": "c"}},
            ],
        )

        await store.truncate("tape-truncate", 2)

        rows = await store.load("tape-truncate")
        assert [cast(dict[str, str], row["payload"])["content"] for row in rows] == [
            "a",
            "b",
        ]

    @pytest.mark.asyncio
    async def test_truncate_rejects_negative_keep(self, store: PGTapeStore):
        with pytest.raises(ValueError, match="keep must be >= 0"):
            await store.truncate("tape-truncate", -1)


class TestPGCheckpointStore:
    @pytest.fixture
    def fake_pool(self) -> FakePool:
        return FakePool()

    @pytest.fixture
    def pool(self, fake_pool: FakePool) -> PGPool:
        async def fake_pool_factory(**_: object) -> FakePool:
            return fake_pool

        return PGPool(dsn="postgresql://example", pool_factory=fake_pool_factory)

    @pytest.fixture
    def store(self, pool: PGPool) -> PGCheckpointStore:
        return PGCheckpointStore(pool=pool)

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

    def test_satisfies_protocol(self, store: PGCheckpointStore):
        assert isinstance(store, CheckpointStore)

    @pytest.mark.asyncio
    async def test_pg_checkpoint_store_round_trip_snapshot(
        self, store: PGCheckpointStore
    ):
        snapshot = self._snapshot(
            "cp-roundtrip",
            "tape-roundtrip",
            created_at=datetime(2026, 4, 17, tzinfo=UTC),
        )

        await store.save(snapshot)

        loaded = await store.load("cp-roundtrip")

        assert loaded == snapshot

    @pytest.mark.asyncio
    async def test_pg_checkpoint_store_overwrites_existing_checkpoint_id(
        self, store: PGCheckpointStore
    ):
        first_snapshot = self._snapshot(
            "cp-retry",
            "tape-first",
            created_at=datetime(2026, 4, 17, tzinfo=UTC),
        )
        second_snapshot = self._snapshot(
            "cp-retry",
            "tape-second",
            created_at=datetime(2026, 4, 17, 1, tzinfo=UTC),
        )
        second_snapshot = CheckpointSnapshot(
            meta=CheckpointMeta(
                checkpoint_id=second_snapshot.meta.checkpoint_id,
                tape_id=second_snapshot.meta.tape_id,
                session_id="session-2",
                entry_count=4,
                window_start=2,
                created_at=second_snapshot.meta.created_at,
                label="retry-save",
            ),
            tape_entries=(
                {
                    "id": "e-3",
                    "kind": "message",
                    "payload": {"content": "retry"},
                    "timestamp": 3.0,
                },
            ),
            plugin_states={"topic": {"current": "retry"}},
            extra={"source": "retry"},
        )

        await store.save(first_snapshot)
        await store.save(second_snapshot)

        loaded = await store.load("cp-retry")

        assert loaded == second_snapshot

    @pytest.mark.asyncio
    async def test_pg_checkpoint_store_list_by_tape_returns_sorted_meta(
        self, store: PGCheckpointStore
    ):
        later = self._snapshot(
            "cp-later",
            "tape-a",
            created_at=datetime(2026, 4, 17, 1, tzinfo=UTC),
        )
        earlier = self._snapshot(
            "cp-earlier",
            "tape-a",
            created_at=datetime(2026, 4, 17, 0, tzinfo=UTC),
        )
        other = self._snapshot(
            "cp-other",
            "tape-b",
            created_at=datetime(2026, 4, 17, 2, tzinfo=UTC),
        )

        await store.save(later)
        await store.save(earlier)
        await store.save(other)

        listed = await store.list_by_tape("tape-a")

        assert listed == [earlier.meta, later.meta]

    @pytest.mark.asyncio
    async def test_pg_checkpoint_store_delete_removes_snapshot(
        self, store: PGCheckpointStore
    ):
        snapshot = self._snapshot(
            "cp-delete",
            "tape-delete",
            created_at=datetime(2026, 4, 17, tzinfo=UTC),
        )

        await store.save(snapshot)
        await store.delete("cp-delete")

        assert await store.load("cp-delete") is None

    @pytest.mark.asyncio
    async def test_pg_checkpoint_store_schema_created_once(
        self, store: PGCheckpointStore, fake_pool: FakePool
    ):
        await store.save(
            self._snapshot(
                "cp-one",
                "tape-one",
                created_at=datetime(2026, 4, 17, tzinfo=UTC),
            )
        )
        await store.save(
            self._snapshot(
                "cp-two",
                "tape-two",
                created_at=datetime(2026, 4, 17, 1, tzinfo=UTC),
            )
        )

        schema_calls = [
            query
            for query, _ in fake_pool.executed
            if "CREATE TABLE IF NOT EXISTS agent_checkpoints" in query
        ]
        assert len(schema_calls) == 1

    @pytest.mark.asyncio
    async def test_pg_checkpoint_store_save_passes_python_objects_to_codec_enabled_pool(
        self, store: PGCheckpointStore, fake_pool: FakePool
    ):
        snapshot = self._snapshot(
            "checkpoint-codec",
            "tape-codec",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )

        await store.save(snapshot)

        insert_calls = [
            args
            for query, args in fake_pool.executed
            if "INSERT INTO agent_checkpoints" in query
        ]
        assert len(insert_calls) == 1
        assert isinstance(insert_calls[0][2], dict)
        assert isinstance(insert_calls[0][3], list)
        assert isinstance(insert_calls[0][4], dict)
        assert isinstance(insert_calls[0][5], dict)


class MockPoolForLock:
    def __init__(self) -> None:
        self._conn = FakePoolConnection()
        self.release = AsyncMock()

    async def acquire(self) -> FakePoolConnection:
        return self._conn


class FakePoolConnection:
    def __init__(self) -> None:
        self.execute = AsyncMock()


class TestPGSessionLock:
    @pytest.fixture
    def mock_pool(self) -> MockPoolForLock:
        return MockPoolForLock()

    @pytest.fixture
    def lock(self, mock_pool: MockPoolForLock) -> PGSessionLock:
        return PGSessionLock(pool=mock_pool)

    @pytest.mark.asyncio
    async def test_acquire_calls_advisory_lock(
        self, lock: PGSessionLock, mock_pool: MockPoolForLock
    ):
        await lock.acquire("session-abc")

        calls = mock_pool._conn.execute.call_args_list
        assert len(calls) == 1
        sql = calls[0][0][0]
        assert "pg_advisory_lock" in sql
        assert "hashtext" in sql

    @pytest.mark.asyncio
    async def test_release_unlocks_and_returns_connection(
        self, lock: PGSessionLock, mock_pool: MockPoolForLock
    ):
        await lock.acquire("session-abc")
        mock_pool._conn.execute.reset_mock()

        await lock.release()

        calls = mock_pool._conn.execute.call_args_list
        assert len(calls) == 1
        sql = calls[0][0][0]
        assert "pg_advisory_unlock_all" in sql
        mock_pool.release.assert_awaited_once_with(mock_pool._conn)

    @pytest.mark.asyncio
    async def test_release_without_acquire_is_noop(self, lock: PGSessionLock):
        await lock.release()

    @pytest.mark.asyncio
    async def test_release_returns_connection_even_on_error(
        self, lock: PGSessionLock, mock_pool: MockPoolForLock
    ):
        await lock.acquire("session-abc")
        mock_pool._conn.execute = AsyncMock(side_effect=Exception("unlock failed"))

        with pytest.raises(Exception, match="unlock failed"):
            await lock.release()

        mock_pool.release.assert_awaited_once_with(mock_pool._conn)

    @pytest.mark.asyncio
    async def test_acquire_raises_when_already_held(
        self, lock: PGSessionLock, mock_pool: MockPoolForLock
    ):
        await lock.acquire("session-abc")

        with pytest.raises(RuntimeError, match="already held"):
            await lock.acquire("session-def")

        mock_pool.release.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_acquire_releases_connection_when_lock_sql_fails(
        self, lock: PGSessionLock, mock_pool: MockPoolForLock
    ):
        mock_pool._conn.execute = AsyncMock(side_effect=Exception("lock failed"))

        with pytest.raises(Exception, match="lock failed"):
            await lock.acquire("session-abc")

        mock_pool.release.assert_awaited_once_with(mock_pool._conn)
