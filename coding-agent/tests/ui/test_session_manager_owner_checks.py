from __future__ import annotations

import pytest
from typing import cast

from agentkit.checkpoint import CheckpointService
from coding_agent.ui.session_manager import SessionManager
from datetime import UTC, datetime

from coding_agent.ui.session_owner_store import (
    SessionOwnerRecord,
    SessionOwnerStoreProtocol,
)
from coding_agent.ui.session_store import InMemorySessionStore


class FakeOwnerStore:
    def __init__(self) -> None:
        self._owners: dict[str, SessionOwnerRecord] = {}

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float,
        fencing_token: int,
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
        del lease_seconds, new_fencing_token, current_fencing_token, owner_id
        return session_id in self._owners

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


class FakeCheckpointStore:
    async def save(self, snapshot) -> None:
        del snapshot
        raise AssertionError("unused")

    async def load(self, checkpoint_id: str):
        del checkpoint_id
        raise AssertionError("unused")

    async def list_by_tape(self, tape_id: str):
        del tape_id
        return []

    async def delete(self, checkpoint_id: str) -> None:
        del checkpoint_id


@pytest.mark.asyncio
async def test_run_agent_rejects_non_owner_instance() -> None:
    owner_store = FakeOwnerStore()
    create_agent_calls = 0

    def fail_create_agent(**kwargs):
        nonlocal create_agent_calls
        create_agent_calls += 1
        raise AssertionError(f"should not create agent: {kwargs}")

    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        create_agent_fn=fail_create_agent,
        owner_store=cast(SessionOwnerStoreProtocol, cast(object, owner_store)),
        owner_id="owner-b",
        fencing_token=2,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.run_agent(session_id, "hello")

    assert create_agent_calls == 0
    assert manager.get_session(session_id).turn_in_progress is False


@pytest.mark.asyncio
async def test_restore_checkpoint_rejects_stale_owner() -> None:
    owner_store = FakeOwnerStore()
    restore_calls = 0

    class FailCheckpointService(CheckpointService):
        async def restore(self, checkpoint_id: str):
            nonlocal restore_calls
            restore_calls += 1
            raise AssertionError(f"should not restore checkpoint: {checkpoint_id}")

    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=FailCheckpointService(FakeCheckpointStore()),
        owner_store=cast(SessionOwnerStoreProtocol, cast(object, owner_store)),
        owner_id="owner-b",
        fencing_token=2,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.restore_checkpoint(session_id, "cp-1")

    assert restore_calls == 0


@pytest.mark.asyncio
async def test_close_session_rejects_stale_owner() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=cast(SessionOwnerStoreProtocol, cast(object, owner_store)),
        owner_id="owner-b",
        fencing_token=2,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.close_session(session_id)

    assert manager.has_session(session_id) is True
