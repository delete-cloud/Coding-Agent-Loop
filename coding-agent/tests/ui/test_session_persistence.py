from __future__ import annotations

from datetime import datetime

import pytest

from coding_agent.approval.store import ApprovalStore
from coding_agent.ui.session_manager import Session, SessionManager
from coding_agent.ui.session_store import (
    InMemorySessionStore,
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
