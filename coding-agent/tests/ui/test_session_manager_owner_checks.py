from __future__ import annotations

import asyncio
from datetime import timedelta
import pytest

from agentkit.checkpoint import CheckpointService
from coding_agent.ui.session_manager import SessionManager
from datetime import UTC, datetime

from coding_agent.ui.session_owner_store import (
    SessionOwnerRecord,
)
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
        if session_id in self._owners:
            return False
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


def test_session_manager_rejects_owner_store_without_owner_metadata() -> None:
    with pytest.raises(
        ValueError,
        match="owner_id and fencing_token must be provided when owner_store is set",
    ):
        SessionManager(
            store=InMemorySessionStore(),
            owner_store=FakeOwnerStore(),
        )


def test_session_manager_rejects_owner_metadata_without_owner_store() -> None:
    with pytest.raises(
        ValueError,
        match="owner_store must be provided when owner_id or fencing_token is set",
    ):
        SessionManager(
            store=InMemorySessionStore(),
            owner_id="owner-a",
            fencing_token=1,
        )


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
        owner_store=owner_store,
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
        owner_store=owner_store,
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
        owner_store=owner_store,
        owner_id="owner-b",
        fencing_token=2,
    )
    session_id = await manager.create_session()
    await owner_store.acquire(session_id, "owner-a", 30.0, 1)

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await manager.close_session(session_id)

    assert manager.has_session(session_id) is True


@pytest.mark.asyncio
async def test_run_agent_rejects_expired_owner_lease() -> None:
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
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=1,
    )
    session_id = await manager.create_session()
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        fencing_token=1,
    )

    with pytest.raises(RuntimeError, match="session owner lease expired"):
        await manager.run_agent(session_id, "hello")

    assert create_agent_calls == 0
    assert manager.get_session(session_id).turn_in_progress is False


@pytest.mark.asyncio
async def test_close_session_revalidates_owner_after_waiting_for_lock() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=1,
    )
    session_id = await manager.create_session()
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    lock = manager._lock
    await lock.acquire()

    async def close_with_wait() -> None:
        await manager.close_session(session_id)

    close_task = asyncio.create_task(close_with_wait())
    await asyncio.sleep(0)
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=2,
    )
    lock.release()

    with pytest.raises(RuntimeError, match="stale owner or fencing token rejected"):
        await close_task

    assert manager.has_session(session_id) is True
