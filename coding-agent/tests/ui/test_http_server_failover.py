from __future__ import annotations

import asyncio
import types
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from agentkit.tape.tape import Tape
from coding_agent.ui import http_server
from coding_agent.ui.session_manager import SessionManager
from coding_agent.ui.session_owner_store import SessionOwnerRecord
from coding_agent.ui.session_store import InMemorySessionStore
from coding_agent.wire.protocol import ApprovalRequest, ToolCallDelta


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
        del lease_seconds
        if session_id in self._owners:
            return False
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=datetime.now(UTC),
            fencing_token=fencing_token,
        )
        return True

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        del lease_seconds
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner.owner_id != owner_id or owner.fencing_token != current_fencing_token:
            return False
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=datetime.now(UTC),
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

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        return self._owners.get(session_id)

    def force_owner(self, session_id: str, owner_id: str, fencing_token: int) -> None:
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=datetime.now(UTC),
            fencing_token=fencing_token,
        )


def _approval_request(session_id: str, request_id: str = "req-1") -> ApprovalRequest:
    return ApprovalRequest(
        session_id=session_id,
        request_id=request_id,
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id=f"call-{request_id}",
        ),
        timeout_seconds=30,
    )


@pytest.fixture(autouse=True)
async def reset_http_server_state(monkeypatch):
    original_manager = http_server.session_manager
    http_server.limiter.reset()
    original_manager.clear_sessions()
    yield
    monkeypatch.setattr(http_server, "session_manager", original_manager)
    http_server.limiter.reset()
    original_manager.clear_sessions()


@pytest.fixture
async def client():
    transport = ASGITransport(app=http_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_approve_endpoint_rejects_stale_owner_after_owner_change(
    client, monkeypatch
) -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=1,
    )
    monkeypatch.setattr(http_server, "session_manager", manager)

    session_id = await manager.create_session()
    session = manager.get_session(session_id)
    session.turn_in_progress = True
    session.approval_store.add_request(_approval_request(session_id))
    owner_store.force_owner(session_id, owner_id="owner-b", fencing_token=2)

    response = await client.post(
        f"/sessions/{session_id}/approve",
        json={"request_id": "req-1", "approved": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "stale owner or fencing token rejected"
    assert session.approval_store.get_request("req-1") is not None


@pytest.mark.asyncio
async def test_close_endpoint_rejects_stale_owner_after_owner_change(
    client, monkeypatch
) -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=1,
    )
    monkeypatch.setattr(http_server, "session_manager", manager)

    session_id = await manager.create_session()
    owner_store.force_owner(session_id, owner_id="owner-b", fencing_token=2)

    response = await client.delete(f"/sessions/{session_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "stale owner or fencing token rejected"
    assert manager.has_session(session_id) is True


@pytest.mark.asyncio
async def test_failover_rebuilds_from_persisted_state_without_resuming_local_runtime(
    monkeypatch,
) -> None:
    store = InMemorySessionStore()
    initial_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="persisted-tape"),
        plugin_states={},
    )
    rebuilt_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="persisted-tape"),
        plugin_states={},
    )
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def initialize(self) -> None:
            return None

    create_calls: list[tuple[str | None, str | None]] = []

    def create_initial_agent(**kwargs):
        tape = kwargs.get("tape")
        create_calls.append(
            (kwargs.get("session_id_override"), None if tape is None else tape.tape_id)
        )
        return fake_pipeline, initial_ctx

    def create_failover_agent(**kwargs):
        tape = kwargs.get("tape")
        create_calls.append(
            (kwargs.get("session_id_override"), None if tape is None else tape.tape_id)
        )
        return fake_pipeline, rebuilt_ctx

    monkeypatch.setattr("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter)

    first_manager = SessionManager(store=store, create_agent_fn=create_initial_agent)
    session_id = await first_manager.create_session()
    await first_manager.ensure_session_runtime(session_id)

    original_session = first_manager.get_session(session_id)
    original_session.turn_in_progress = True
    original_session.task = asyncio.create_task(asyncio.sleep(60))
    original_session.event_queues.append(asyncio.Queue())
    original_session.approval_store.add_request(
        _approval_request(session_id, "req-local")
    )
    original_session.pending_approval = {"request_id": "req-local", "tool_name": "bash"}
    await first_manager._persist_session_async(original_session)

    second_manager = SessionManager(store=store, create_agent_fn=create_failover_agent)
    reloaded_session = second_manager.get_session(session_id)

    assert reloaded_session.runtime_pipeline is None
    assert reloaded_session.runtime_ctx is None
    assert reloaded_session.runtime_adapter is None
    assert reloaded_session.task is None
    assert reloaded_session.turn_in_progress is False
    assert reloaded_session.event_queues == []
    assert reloaded_session.pending_approval is None
    assert reloaded_session.approval_store.get_request("req-local") is None

    returned_ctx = await second_manager.ensure_session_runtime(session_id)

    assert returned_ctx is rebuilt_ctx
    assert create_calls == [
        (session_id, None),
        (session_id, "persisted-tape"),
    ]

    original_session.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await original_session.task
