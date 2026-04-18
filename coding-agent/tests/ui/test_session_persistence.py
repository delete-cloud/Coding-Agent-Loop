from __future__ import annotations

import asyncio
from datetime import datetime
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import cast
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from coding_agent.approval.store import ApprovalStore
from coding_agent.ui.session_manager import Session, SessionManager
from coding_agent.ui.session_store import (
    InMemorySessionStore,
    PGSessionMetadataStore,
    RedisSessionStore,
    create_session_store,
)


def test_register_session_uses_public_api() -> None:
    manager = SessionManager()
    approval_store = ApprovalStore()
    session = Session(
        id="test-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=approval_store,
    )

    manager.register_session(session)

    assert manager.has_session("test-session")
    assert manager.get_session("test-session") is session


def test_clear_sessions_uses_public_api() -> None:
    manager = SessionManager()
    session = Session(
        id="test-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
    )
    manager.register_session(session)

    manager.clear_sessions()

    assert manager.list_sessions() == []


@pytest.mark.asyncio
async def test_create_session_persists_to_store_backing() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)

    session_id = await manager.create_session()
    payload = store.load(session_id)

    assert manager.has_session(session_id)
    assert payload is not None
    assert payload["id"] == session_id
    assert store.list_sessions() == [session_id]


def test_create_session_store_warns_and_falls_back_when_redis_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_factory(url: str):
        raise OSError(f"cannot connect to {url}")

    with caplog.at_level("WARNING"):
        store = create_session_store(
            redis_url="redis://example:6379/0",
            redis_client_factory=failing_factory,
        )

    assert isinstance(store, InMemorySessionStore)
    assert "falling back to in-memory" in caplog.text


def test_in_memory_session_store_reports_healthy() -> None:
    assert InMemorySessionStore().check_health() is True


def test_redis_session_store_can_rehydrate_session_metadata() -> None:
    class FakeRedisClient:
        def __init__(self) -> None:
            self._data: dict[str, str] = {}
            self._index: set[str] = set()

        def ping(self) -> bool:
            return True

        def set(self, key: str, value: str) -> None:
            self._data[key] = value

        def get(self, key: str) -> str | None:
            return self._data.get(key)

        def delete(self, key: str) -> None:
            self._data.pop(key, None)

        def sadd(self, key: str, value: str) -> None:
            assert key == "coding-agent:sessions:index"
            self._index.add(value)

        def srem(self, key: str, value: str) -> None:
            assert key == "coding-agent:sessions:index"
            self._index.discard(value)

        def smembers(self, key: str) -> set[str]:
            assert key == "coding-agent:sessions:index"
            return set(self._index)

    client = FakeRedisClient()
    store = RedisSessionStore(client=client, redis_url="redis://test")
    session = Session(
        id="persisted-session",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
    )

    store.save(session.id, session.to_store_data())

    payload = RedisSessionStore(client=client, redis_url="redis://test").load(
        "persisted-session"
    )

    assert payload is not None
    assert payload["id"] == "persisted-session"
    assert "persisted-session" in store.list_sessions()


def test_pg_session_metadata_store_round_trips_session_metadata() -> None:
    class FakeRecord:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def __iter__(self):
            return iter(self._payload.items())

    class FakeAsyncPGPool:
        def __init__(self) -> None:
            self.sessions: dict[str, dict[str, object]] = {}
            self.executed: list[tuple[str, tuple[object, ...]]] = []
            self.closed = False

        async def execute(self, query: str, *args: object) -> str:
            self.executed.append((query, args))
            if "INSERT INTO agent_http_sessions" in query:
                session_id, payload = args
                assert isinstance(session_id, str)
                assert isinstance(payload, dict)
                self.sessions[session_id] = payload
                return "INSERT 0 1"
            if "DELETE FROM agent_http_sessions" in query:
                (session_id,) = args
                assert isinstance(session_id, str)
                self.sessions.pop(session_id, None)
                return "DELETE 1"
            if "CREATE TABLE IF NOT EXISTS agent_http_sessions" in query:
                return "CREATE TABLE"
            raise AssertionError(f"unexpected execute query: {query}")

        async def fetchrow(self, query: str, *args: object) -> object | None:
            self.executed.append((query, args))
            if "SELECT payload FROM agent_http_sessions" in query:
                (session_id,) = args
                assert isinstance(session_id, str)
                payload = self.sessions.get(session_id)
                return None if payload is None else FakeRecord({"payload": payload})
            if query.strip() == "SELECT 1":
                return FakeRecord({"?column?": 1})
            raise AssertionError(f"unexpected fetchrow query: {query}")

        async def fetch(self, query: str, *args: object) -> list[object]:
            self.executed.append((query, args))
            if "SELECT session_id FROM agent_http_sessions" in query:
                return [
                    FakeRecord({"session_id": session_id})
                    for session_id in sorted(self.sessions.keys())
                ]
            raise AssertionError(f"unexpected fetch query: {query}")

        async def acquire(self):
            raise AssertionError("unused")

        async def release(self, connection):
            _ = connection
            raise AssertionError("unused")

        async def close(self) -> None:
            self.closed = True

    class FakePGPool:
        def __init__(self) -> None:
            self.pool = FakeAsyncPGPool()

        async def get_pool(self) -> FakeAsyncPGPool:
            return self.pool

        async def close(self) -> None:
            await self.pool.close()

    pg_pool = FakePGPool()
    store = PGSessionMetadataStore(pool=pg_pool)
    try:
        session = Session(
            id="pg-session",
            created_at=datetime.now(),
            last_activity=datetime.now(),
            approval_store=ApprovalStore(),
        )

        store.save(session.id, session.to_store_data())

        payload = store.load("pg-session")

        assert payload is not None
        assert payload["id"] == "pg-session"
        assert store.list_sessions() == ["pg-session"]
        assert store.check_health() is True
        insert_calls = [
            args
            for query, args in pg_pool.pool.executed
            if "INSERT INTO agent_http_sessions" in query
        ]
        assert len(insert_calls) == 1
        assert isinstance(insert_calls[0][1], dict)
    finally:
        store.close()


def test_pg_session_metadata_store_count_sessions_uses_count_query() -> None:
    class FakeRecord:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def __iter__(self):
            return iter(self._payload.items())

    class FakeAsyncPGPool:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple[object, ...]]] = []
            self.closed = False

        async def execute(self, query: str, *args: object) -> str:
            self.executed.append((query, args))
            if "CREATE TABLE IF NOT EXISTS agent_http_sessions" in query:
                return "CREATE TABLE"
            raise AssertionError(f"unexpected execute query: {query}")

        async def fetchrow(self, query: str, *args: object) -> object | None:
            self.executed.append((query, args))
            if "SELECT COUNT(*) AS session_count FROM agent_http_sessions" in query:
                return FakeRecord({"session_count": 3})
            raise AssertionError(f"unexpected fetchrow query: {query}")

        async def fetch(self, query: str, *args: object) -> list[object]:
            self.executed.append((query, args))
            raise AssertionError(f"unexpected fetch query: {query}")

        async def acquire(self):
            raise AssertionError("unused")

        async def release(self, connection):
            _ = connection
            raise AssertionError("unused")

        async def close(self) -> None:
            self.closed = True

    class FakePGPool:
        def __init__(self) -> None:
            self.pool = FakeAsyncPGPool()

        async def get_pool(self) -> FakeAsyncPGPool:
            return self.pool

        async def close(self) -> None:
            await self.pool.close()

    pg_pool = FakePGPool()
    store = PGSessionMetadataStore(pool=pg_pool)
    try:
        assert store.count_sessions() == 3
        count_calls = [
            args
            for query, args in pg_pool.pool.executed
            if "SELECT COUNT(*) AS session_count FROM agent_http_sessions" in query
        ]
        assert count_calls == [()]
    finally:
        store.close()


def test_pg_session_metadata_store_waits_for_loop_start_before_returning() -> None:
    created_loops: list[FakeLoop] = []
    created_threads: list[FakeThread] = []

    class FakePool:
        async def get_pool(self) -> object:
            raise AssertionError("unused")

        async def close(self) -> None:
            return None

    class FakeLoop:
        def __init__(self) -> None:
            self.closed = False
            created_loops.append(self)

        def run_forever(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def call_soon_threadsafe(self, callback) -> None:
            callback()

        def close(self) -> None:
            self.closed = True

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
            created_threads.append(self)

        def start(self) -> None:
            self.started = True
            self.target()

        def join(self, timeout: float | None = None) -> None:
            _ = timeout

        def is_alive(self) -> bool:
            return False

    with (
        patch(
            "coding_agent.ui.session_store.asyncio.new_event_loop", side_effect=FakeLoop
        ),
        patch("coding_agent.ui.session_store.asyncio.set_event_loop"),
        patch("coding_agent.ui.session_store.threading.Thread", side_effect=FakeThread),
    ):
        store = PGSessionMetadataStore(pool=FakePool())

    try:
        assert len(created_loops) == 1
        assert len(created_threads) == 1
        assert created_threads[0].started is True
        assert store._loop_ready.is_set() is True
    finally:
        store._run_sync = lambda operation: (operation.close(), None)[1]
        store.close()


def test_pg_session_metadata_store_closes_background_loop_and_pool() -> None:
    class FakeAsyncPGPool:
        def __init__(self) -> None:
            self.closed = False

        async def execute(self, query: str, *args: object) -> str:
            _ = (query, args)
            raise AssertionError("unused")

        async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
            _ = (query, args)
            raise AssertionError("unused")

        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            _ = (query, args)
            raise AssertionError("unused")

        async def acquire(self):
            raise AssertionError("unused")

        async def release(self, connection):
            _ = connection
            raise AssertionError("unused")

        async def close(self) -> None:
            self.closed = True

    class FakePGPool:
        def __init__(self) -> None:
            self.pool = FakeAsyncPGPool()

        async def get_pool(self) -> FakeAsyncPGPool:
            return self.pool

        async def close(self) -> None:
            await self.pool.close()

    pool = FakePGPool()
    store = PGSessionMetadataStore(pool=pool)

    store.close()

    assert pool.pool.closed is True


def test_pg_session_metadata_store_skips_loop_close_when_thread_does_not_stop() -> None:
    class FakePool:
        async def get_pool(self) -> object:
            raise AssertionError("unused")

        async def close(self) -> None:
            return None

    class FakeThread:
        def __init__(self) -> None:
            self.join_calls: list[float | None] = []

        def start(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(timeout)

        def is_alive(self) -> bool:
            return True

    class FakeLoop:
        def __init__(self) -> None:
            self.closed = False
            self.stop_calls = 0

        def run_forever(self) -> None:
            return None

        def stop(self) -> None:
            self.stop_calls += 1

        def call_soon_threadsafe(self, callback) -> None:
            callback()

        def close(self) -> None:
            self.closed = True

    store = PGSessionMetadataStore(pool=FakePool())
    fake_thread = FakeThread()
    fake_loop = FakeLoop()

    def fake_run_sync(operation):
        operation.close()
        return None

    with (
        patch.object(store, "_loop_thread", fake_thread),
        patch.object(store, "_loop", fake_loop),
        patch.object(store, "_run_sync", fake_run_sync),
    ):
        store.close()

    assert fake_thread.join_calls == [5]
    assert fake_loop.stop_calls == 1
    assert fake_loop.closed is False


def test_pg_session_metadata_store_close_test_does_not_start_real_thread() -> None:
    created_loops: list[FakeLoop] = []
    created_threads: list[FakeThread] = []

    class FakePool:
        async def get_pool(self) -> object:
            raise AssertionError("unused")

        async def close(self) -> None:
            return None

    class FakeThread:
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            self.target = cast(object, target)
            self.name = name
            self.daemon = daemon
            self.started = False
            self.join_calls: list[float | None] = []
            created_threads.append(self)

        def start(self) -> None:
            self.started = True

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(timeout)

        def is_alive(self) -> bool:
            return True

    class FakeLoop:
        def __init__(self) -> None:
            self.closed = False
            self.stop_calls = 0
            created_loops.append(self)

        def run_forever(self) -> None:
            return None

        def stop(self) -> None:
            self.stop_calls += 1

        def call_soon_threadsafe(self, callback) -> None:
            callback()

        def close(self) -> None:
            self.closed = True

    def fake_run_sync(operation: object) -> None:
        cast(object, operation)
        close = getattr(operation, "close")
        cast(object, close)
        close()
        return None

    with (
        patch(
            "coding_agent.ui.session_store.asyncio.new_event_loop", side_effect=FakeLoop
        ),
        patch("coding_agent.ui.session_store.threading.Thread", side_effect=FakeThread),
    ):
        with pytest.raises(
            RuntimeError, match="postgres session metadata loop thread failed to start"
        ):
            PGSessionMetadataStore(pool=FakePool())

    assert len(created_loops) == 1
    assert len(created_threads) == 1
    assert created_threads[0].started is True
    assert created_threads[0].join_calls == [0.1]
    assert created_loops[0].stop_calls == 1
    assert created_loops[0].closed is False


def test_pg_session_metadata_store_run_sync_times_out_and_cancels_future() -> None:
    class FakePool:
        async def get_pool(self) -> object:
            raise AssertionError("unused")

        async def close(self) -> None:
            return None

    future = MagicMock()
    future.result.side_effect = FutureTimeoutError()

    store = PGSessionMetadataStore(pool=FakePool())
    try:
        operation = asyncio.sleep(0)
        with patch(
            "coding_agent.ui.session_store.asyncio.run_coroutine_threadsafe",
            return_value=future,
        ):
            with pytest.raises(
                TimeoutError, match="postgres session metadata operation timed out"
            ):
                store._run_sync(operation)

        future.cancel.assert_called_once_with()
        operation.close()
    finally:
        store.close()


def test_pg_session_metadata_store_run_sync_raises_clear_error_when_loop_unavailable() -> (
    None
):
    class FakePool:
        async def get_pool(self) -> object:
            raise AssertionError("unused")

        async def close(self) -> None:
            return None

    store = PGSessionMetadataStore(pool=FakePool())
    try:
        operation = asyncio.sleep(0)
        with patch(
            "coding_agent.ui.session_store.asyncio.run_coroutine_threadsafe",
            side_effect=RuntimeError("loop closed"),
        ):
            with pytest.raises(
                RuntimeError,
                match="postgres session metadata loop is not available",
            ):
                store._run_sync(operation)

        assert operation.cr_frame is None
    finally:
        store.close()


def test_create_session_store_strips_dsn_before_building_pg_pool() -> None:
    with patch("coding_agent.ui.session_store.PGPool") as pg_pool_cls:
        store = create_session_store(backend="pg", dsn="  postgresql://example  ")
    assert isinstance(store, PGSessionMetadataStore)
    try:
        assert pg_pool_cls.call_args.kwargs["dsn"] == "postgresql://example"
    finally:
        store._loop.call_soon_threadsafe(store._loop.stop)
        store._loop_thread.join(timeout=5)
        if not store._loop_thread.is_alive():
            store._loop.close()


def test_create_session_store_rejects_all_whitespace_dsn() -> None:
    with pytest.raises(ValueError, match="PG session store requires dsn or pg_pool"):
        create_session_store(backend="pg", dsn="   ")


def test_create_session_store_builds_pg_store_from_explicit_backend() -> None:
    store = create_session_store(backend="pg", dsn="postgresql://example")
    assert isinstance(store, PGSessionMetadataStore)
    try:
        assert isinstance(store, PGSessionMetadataStore)
    finally:
        store.close()


def test_create_session_store_accepts_injected_pg_pool_without_dsn() -> None:
    class MockPGPool:
        def __init__(self) -> None:
            self.closed = False

        async def get_pool(self) -> object:
            raise AssertionError("unused")

        async def close(self) -> None:
            self.closed = True

    pool = MockPGPool()
    store = create_session_store(backend="pg", pg_pool=pool)
    assert isinstance(store, PGSessionMetadataStore)
    try:
        assert store._pool is pool
    finally:
        store.close()

    assert pool.closed is True


def test_rehydrate_clears_non_restart_safe_runtime_state() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session = Session(
        id="rehydrate-me",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
        turn_in_progress=True,
        pending_approval={"request_id": "req-123", "tool_name": "bash"},
        approval_response={"decision": "approve", "feedback": "ok"},
    )
    manager.register_session(session)

    reloaded = SessionManager(store=store).get_session("rehydrate-me")

    assert reloaded.turn_in_progress is False
    assert reloaded.pending_approval is None
    assert reloaded.approval_response is None


def test_redis_session_store_reports_health_from_ping() -> None:
    class FakeRedisClient:
        def ping(self) -> bool:
            return True

        def set(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def get(self, key: str) -> str | None:
            raise AssertionError("unused")

        def delete(self, key: str) -> None:
            raise AssertionError("unused")

        def sadd(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def srem(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def smembers(self, key: str) -> set[str]:
            raise AssertionError("unused")

    store = RedisSessionStore(client=FakeRedisClient(), redis_url="redis://test")

    assert store.check_health() is True


def test_redis_session_store_reports_unhealthy_on_ping_error() -> None:
    class FailingRedisClient:
        def __init__(self) -> None:
            self._first = True

        def ping(self) -> bool:
            if self._first:
                self._first = False
                return True
            raise OSError("redis unavailable")

        def set(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def get(self, key: str) -> str | None:
            raise AssertionError("unused")

        def delete(self, key: str) -> None:
            raise AssertionError("unused")

        def sadd(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def srem(self, key: str, value: str) -> None:
            raise AssertionError("unused")

        def smembers(self, key: str) -> set[str]:
            raise AssertionError("unused")

    store = RedisSessionStore(client=FailingRedisClient(), redis_url="redis://test")

    assert store.check_health() is False
