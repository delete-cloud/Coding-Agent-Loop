from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Callable
from typing import Any
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import HTTPException
from starlette.requests import Request

from coding_agent.ui.http_server import (
    _broadcast_event,
    app,
    get_events,
    limiter,
    session_manager,
)
from coding_agent.ui.session_owner_store import SessionOwnerRecord
from coding_agent.ui.session_store import InMemorySessionStore


class FakeOwnerStore:
    def __init__(self) -> None:
        self._owners: dict[str, SessionOwnerRecord] = {}

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
            fencing_token=fencing_token,
        )
        return True

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        return self._owners.get(session_id)

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner.owner_id != owner_id or owner.fencing_token != current_fencing_token:
            return False
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
            fencing_token=new_fencing_token,
        )
        return True

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner.owner_id != owner_id or owner.fencing_token != fencing_token:
            return False
        del self._owners[session_id]
        return True


@pytest.fixture(autouse=True)
async def clear_sessions():
    session_manager.clear_sessions()
    limiter.reset()
    yield
    session_manager.clear_sessions()
    limiter.reset()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def owner_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOwnerStore]:
    fake_owner_store = FakeOwnerStore()
    original_owner_store = session_manager._owner_store
    original_owner_id = session_manager._owner_id
    original_fencing_token = session_manager._fencing_token

    monkeypatch.setattr(session_manager, "_store", InMemorySessionStore())
    monkeypatch.setattr(session_manager, "_session_cache", {})
    monkeypatch.setattr(session_manager, "_owner_store", fake_owner_store)
    monkeypatch.setattr(session_manager, "_owner_id", "owner-a")
    monkeypatch.setattr(session_manager, "_fencing_token", 1)

    yield fake_owner_store

    monkeypatch.setattr(session_manager, "_owner_store", original_owner_store)
    monkeypatch.setattr(session_manager, "_owner_id", original_owner_id)
    monkeypatch.setattr(session_manager, "_fencing_token", original_fencing_token)


def _events_request(session_id: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/sessions/{session_id}/events",
            "headers": [],
        }
    )


class FakeEventSourceResponse:
    def __init__(self, body_iterator: AsyncIterator[dict[str, str]]):
        self.body_iterator = body_iterator


@pytest.mark.asyncio
async def test_clear_sessions_fixture_resets_limiter_in_teardown(
    monkeypatch: pytest.MonkeyPatch,
):
    session_clear_calls = 0
    limiter_reset_calls = 0

    def fake_clear_sessions() -> None:
        nonlocal session_clear_calls
        session_clear_calls += 1

    def fake_limiter_reset() -> None:
        nonlocal limiter_reset_calls
        limiter_reset_calls += 1

    monkeypatch.setattr(session_manager, "clear_sessions", fake_clear_sessions)
    monkeypatch.setattr(limiter, "reset", fake_limiter_reset)

    fixture_factory = cast(
        "Callable[[], AsyncGenerator[None, None]]",
        getattr(clear_sessions, "__wrapped__"),
    )
    fixture_gen = fixture_factory()
    await anext(fixture_gen)

    assert session_clear_calls == 1
    assert limiter_reset_calls == 1

    with pytest.raises(StopAsyncIteration):
        await anext(fixture_gen)

    assert session_clear_calls == 2
    assert limiter_reset_calls == 2


@pytest.mark.asyncio
async def test_get_events_returns_404_before_owner_check_for_missing_session(
    owner_store: FakeOwnerStore,
):
    del owner_store
    with pytest.raises(HTTPException) as exc_info:
        await get_events(_events_request("missing-session"), "missing-session", None)

    response = exc_info.value
    assert getattr(response, "status_code", None) == 404
    assert getattr(response, "detail", None) == "Session not found: missing-session"


@pytest.mark.asyncio
async def test_get_events_rejects_stale_owner_before_stream_registration(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=2,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_events(_events_request(session_id), session_id, None)

    response = exc_info.value
    assert getattr(response, "status_code", None) == 409
    assert getattr(response, "detail", None) == "stale owner or fencing token rejected"


@pytest.mark.asyncio
async def test_get_events_stops_stream_after_owner_change(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    monkeypatch.setattr(
        "coding_agent.ui.http_server.EventSourceResponse", FakeEventSourceResponse
    )

    real_wait_for = asyncio.wait_for
    timeout_count = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal timeout_count
        if timeout == 30.0:
            awaitable.close()
            timeout_count += 1
            if timeout_count == 1:
                owner_store._owners[session_id] = SessionOwnerRecord(
                    owner_id="owner-b",
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    fencing_token=2,
                )
                raise asyncio.TimeoutError
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr("coding_agent.ui.http_server.asyncio.wait_for", fake_wait_for)

    response = await get_events(_events_request(session_id), session_id, None)
    event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)

    with pytest.raises(StopAsyncIteration):
        await anext(event_generator)


@pytest.mark.asyncio
async def test_get_events_drops_queued_event_after_owner_change(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    monkeypatch.setattr(
        "coding_agent.ui.http_server.EventSourceResponse", FakeEventSourceResponse
    )

    original_verify = session_manager.verify_event_stream_ownership
    verify_calls = 0

    async def fake_verify_event_stream_ownership(current_session_id: str) -> None:
        nonlocal verify_calls
        assert current_session_id == session_id
        verify_calls += 1
        if verify_calls == 1:
            owner_store._owners[session_id] = SessionOwnerRecord(
                owner_id="owner-b",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                fencing_token=2,
            )
        await original_verify(current_session_id)

    monkeypatch.setattr(
        session_manager,
        "verify_event_stream_ownership",
        fake_verify_event_stream_ownership,
    )

    response = await get_events(_events_request(session_id), session_id, None)
    event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)
    session = session_manager.get_session(session_id)
    await session.event_queues[0].put({"event": "message", "data": "stale"})

    with pytest.raises(StopAsyncIteration):
        await anext(event_generator)


@pytest.mark.asyncio
async def test_get_events_returns_404_when_session_disappears_during_queue_registration(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    original_authorize = session_manager.authorize_event_stream

    async def fake_authorize_event_stream(current_session_id: str) -> None:
        assert current_session_id == session_id
        await original_authorize(current_session_id)
        session_manager.clear_sessions()

    monkeypatch.setattr(
        session_manager,
        "authorize_event_stream",
        fake_authorize_event_stream,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_events(_events_request(session_id), session_id, None)

    response = exc_info.value
    assert response.status_code == 404
    assert response.detail == f"Session not found: {session_id}"


@pytest.mark.asyncio
async def test_register_owned_event_queue_cleans_up_queue_on_unexpected_error(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    original_assert_owner = session_manager._assert_owner
    assert_owner_calls = 0
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)

    async def fake_assert_owner(current_session_id: str) -> None:
        nonlocal assert_owner_calls
        assert current_session_id == session_id
        assert_owner_calls += 1
        if assert_owner_calls == 2:
            raise RuntimeError("owner store decode failed")
        await original_assert_owner(current_session_id)

    monkeypatch.setattr(
        session_manager,
        "_assert_owner",
        fake_assert_owner,
    )

    with pytest.raises(RuntimeError, match="owner store decode failed"):
        await session_manager.register_owned_event_queue_async(session_id, queue)

    assert queue not in session_manager.get_session(session_id).event_queues


@pytest.mark.asyncio
async def test_get_events_rejects_owner_change_after_queue_registration(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    original_assert_owner = session_manager._assert_owner
    assert_owner_calls = 0

    async def fake_assert_owner(current_session_id: str) -> None:
        nonlocal assert_owner_calls
        assert current_session_id == session_id
        assert_owner_calls += 1
        if assert_owner_calls == 3:
            owner_store._owners[session_id] = SessionOwnerRecord(
                owner_id="owner-b",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                fencing_token=2,
            )
        await original_assert_owner(current_session_id)

    monkeypatch.setattr(
        session_manager,
        "_assert_owner",
        fake_assert_owner,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_events(_events_request(session_id), session_id, None)

    response = exc_info.value
    assert response.status_code == 409
    assert response.detail == "stale owner or fencing token rejected"
    assert session_manager.get_session(session_id).event_queues == []


@pytest.mark.asyncio
async def test_get_events_rejects_broadcast_during_append_to_recheck_window(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    original_assert_owner = session_manager._assert_owner
    assert_owner_calls = 0

    async def fake_assert_owner(current_session_id: str) -> None:
        nonlocal assert_owner_calls
        assert current_session_id == session_id
        assert_owner_calls += 1
        if assert_owner_calls == 3:
            session = session_manager.get_session(session_id)
            await _broadcast_event(session, {"event": "message", "data": "stale"})
            owner_store._owners[session_id] = SessionOwnerRecord(
                owner_id="owner-b",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                fencing_token=2,
            )
        await original_assert_owner(current_session_id)

    monkeypatch.setattr(
        session_manager,
        "_assert_owner",
        fake_assert_owner,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_events(_events_request(session_id), session_id, None)

    response = exc_info.value
    assert response.status_code == 409
    assert response.detail == "stale owner or fencing token rejected"
    assert session_manager.get_session(session_id).event_queues == []


@pytest.mark.asyncio
async def test_get_events_rejects_close_during_append_to_recheck_window(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    original_close_runtime = session_manager._close_runtime
    close_started = asyncio.Event()
    allow_close_to_finish = asyncio.Event()

    async def fake_close_runtime(session) -> None:
        close_started.set()
        await allow_close_to_finish.wait()
        await original_close_runtime(session)

    monkeypatch.setattr(
        session_manager,
        "_close_runtime",
        fake_close_runtime,
    )

    remove_task = asyncio.create_task(session_manager.remove_session_async(session_id))
    await close_started.wait()

    get_events_task = asyncio.create_task(
        get_events(_events_request(session_id), session_id, None)
    )

    allow_close_to_finish.set()
    await remove_task

    with pytest.raises(HTTPException) as exc_info:
        await get_events_task

    response = exc_info.value
    assert response.status_code == 404
    assert response.detail == f"Session not found: {session_id}"


@pytest.mark.asyncio
async def test_get_events_keeps_stream_alive_for_current_owner(
    client: AsyncClient,
    owner_store: FakeOwnerStore,
    monkeypatch: pytest.MonkeyPatch,
):
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    monkeypatch.setattr(
        "coding_agent.ui.http_server.EventSourceResponse", FakeEventSourceResponse
    )

    real_wait_for = asyncio.wait_for
    timeout_count = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal timeout_count
        if timeout == 30.0:
            awaitable.close()
            timeout_count += 1
            if timeout_count == 1:
                raise asyncio.TimeoutError
            owner_store._owners[session_id] = SessionOwnerRecord(
                owner_id="owner-b",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                fencing_token=2,
            )
            raise asyncio.TimeoutError
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr("coding_agent.ui.http_server.asyncio.wait_for", fake_wait_for)

    response = await get_events(_events_request(session_id), session_id, None)
    event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)

    event = await anext(event_generator)
    assert event == {"event": "ping", "data": ""}

    with pytest.raises(StopAsyncIteration):
        await anext(event_generator)
