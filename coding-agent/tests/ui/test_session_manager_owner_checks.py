from __future__ import annotations

import asyncio
from datetime import timedelta
import pytest

from agentkit.checkpoint import CheckpointService
from coding_agent.ui.session_manager import SessionManager
import types
from unittest.mock import patch
from datetime import UTC, datetime
from coding_agent.ui.session_owner_store import SessionOwnershipConflictError
from coding_agent.ui.session_owner_store import SessionOwnershipConflictReason

from coding_agent.ui.session_owner_store import (
    SessionOwnerRecord,
)
from coding_agent.ui.session_store import InMemorySessionStore
from agentkit.tape.tape import Tape


class DeleteFailingSessionStore(InMemorySessionStore):
    def delete(self, session_id: str) -> None:
        del session_id
        raise RuntimeError("session store delete failed")


class CancellationDeleteSessionStore(InMemorySessionStore):
    def delete(self, session_id: str) -> None:
        del session_id
        raise asyncio.CancelledError


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


class RecordingOwnerStore(FakeOwnerStore):
    def __init__(self) -> None:
        super().__init__()
        self.get_owner_calls: list[str] = []
        self.release_calls: list[str] = []
        self.renew_calls: list[str] = []

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        self.get_owner_calls.append(session_id)
        return await super().get_owner(session_id)

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        self.release_calls.append(session_id)
        return await super().release(session_id, owner_id, fencing_token)

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        self.renew_calls.append(session_id)
        return await super().renew(
            session_id,
            owner_id,
            lease_seconds,
            new_fencing_token,
            current_fencing_token,
        )


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
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
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
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
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
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
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

    with pytest.raises(
        SessionOwnershipConflictError,
        match="session owner lease expired",
    ) as exc_info:
        await manager.run_agent(session_id, "hello")

    assert exc_info.value.reason == SessionOwnershipConflictReason.EXPIRED_LEASE

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

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
        await close_task

    assert manager.has_session(session_id) is True


@pytest.mark.asyncio
async def test_ensure_session_runtime_rejects_stale_owner() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-b",
        fencing_token=2,
    )
    session_id = await manager.create_session()
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    fake_ctx = types.SimpleNamespace(
        config={"tool_registry": object()},
        tape=Tape(tape_id="bootstrapped-tape"),
        plugin_states={},
    )
    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        ),
        _directive_executor=None,
    )

    class FailAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, ctx, consumer
            raise AssertionError("should not initialize runtime for stale owner")

    with (
        patch("importlib.import_module") as import_module,
        patch.dict(
            SessionManager.ensure_session_runtime.__globals__,
            {"PipelineAdapter": FailAdapter},
        ),
    ):
        import_module.return_value = types.SimpleNamespace(
            create_agent=lambda **kwargs: (fake_pipeline, fake_ctx)
        )
        with pytest.raises(
            SessionOwnershipConflictError,
            match="stale owner or fencing token rejected",
        ):
            await manager.ensure_session_runtime(session_id)


@pytest.mark.asyncio
async def test_submit_approval_rejects_stale_owner() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-b",
        fencing_token=2,
    )
    session_id = await manager.create_session()
    owner_store._owners[session_id] = SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=1,
    )

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
        await manager.submit_approval(
            session_id=session_id,
            request_id="req-1",
            approved=True,
        )


@pytest.mark.asyncio
async def test_create_session_acquires_owner_when_owner_store_is_configured() -> None:
    owner_store = FakeOwnerStore()
    owner_lease_seconds = 45.0
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
        owner_lease_seconds=owner_lease_seconds,
    )

    now = datetime.now(UTC)
    session_id = await manager.create_session()

    owner = await owner_store.get_owner(session_id)
    assert owner is not None
    assert owner.owner_id == "owner-a"
    assert owner.fencing_token == 7
    expected_expiry = now + timedelta(seconds=owner_lease_seconds)
    assert abs((owner.lease_expires_at - expected_expiry).total_seconds()) < 2.0


@pytest.mark.asyncio
async def test_create_session_rolls_back_persisted_session_when_owner_acquire_fails() -> None:
    owner_store = FakeOwnerStore()
    store = InMemorySessionStore()
    manager = SessionManager(
        store=store,
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )

    async def reject_acquire(
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        del session_id, owner_id, lease_seconds, fencing_token
        return False

    owner_store.acquire = reject_acquire

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
        await manager.create_session()

    assert store.list_sessions() == []
    assert manager.list_sessions() == []


@pytest.mark.asyncio
async def test_create_session_preserves_owner_error_when_rollback_delete_fails(
    caplog,
) -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=DeleteFailingSessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )

    async def reject_acquire(
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        del session_id, owner_id, lease_seconds, fencing_token
        return False

    owner_store.acquire = reject_acquire

    with pytest.raises(
        SessionOwnershipConflictError,
        match="stale owner or fencing token rejected",
    ):
        await manager.create_session()

    assert manager._session_cache == {}
    assert "Failed to delete partially created session during rollback" in caplog.text


@pytest.mark.asyncio
async def test_create_session_does_not_log_rollback_delete_cancellation(caplog) -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=CancellationDeleteSessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )

    async def reject_acquire(
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        del session_id, owner_id, lease_seconds, fencing_token
        return False

    owner_store.acquire = reject_acquire

    with pytest.raises(SessionOwnershipConflictError):
        await manager.create_session()

    assert manager._session_cache == {}
    assert "Failed to delete partially created session during rollback" not in caplog.text


@pytest.mark.asyncio
async def test_release_owned_sessions_releases_current_owner_only() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    session_id = await manager.create_session()
    owner_store._owners["other-session"] = SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=8,
    )

    await manager.release_owned_sessions()

    assert await owner_store.get_owner(session_id) is None
    other_owner = await owner_store.get_owner("other-session")
    assert other_owner is not None
    assert other_owner == SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=other_owner.lease_expires_at,
        fencing_token=8,
    )


@pytest.mark.asyncio
async def test_release_owned_sessions_logs_failed_owner_release(caplog) -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    session_id = await manager.create_session()

    async def reject_release(
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        assert session_id == expected_session_id
        assert owner_id == "owner-a"
        assert fencing_token == 7
        return False

    expected_session_id = session_id
    owner_store.release = reject_release

    await manager.release_owned_sessions()

    assert "Failed to release owner lease" in caplog.text
    assert session_id in caplog.text
    assert "owner-a" in caplog.text


@pytest.mark.asyncio
async def test_release_owned_sessions_skips_sessions_owned_by_other_replicas() -> None:
    owner_store = RecordingOwnerStore()
    store = InMemorySessionStore()
    manager = SessionManager(
        store=store,
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    owned_session_id = await manager.create_session()
    store.save("other-session", {})
    owner_store._owners["other-session"] = SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=8,
    )

    await manager.release_owned_sessions()

    assert owner_store.get_owner_calls == [owned_session_id, "other-session"]
    assert owner_store.release_calls == [owned_session_id]


@pytest.mark.asyncio
async def test_renew_owner_leases_skips_sessions_owned_by_other_replicas() -> None:
    owner_store = RecordingOwnerStore()
    store = InMemorySessionStore()
    manager = SessionManager(
        store=store,
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    owned_session_id = await manager.create_session()
    store.save("other-session", {})
    owner_store._owners["other-session"] = SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        fencing_token=8,
    )

    await manager.renew_owner_leases()

    assert owner_store.get_owner_calls == [owned_session_id, "other-session"]
    assert owner_store.renew_calls == [owned_session_id]


@pytest.mark.asyncio
async def test_release_owned_sessions_logs_and_continues_after_release_exception(
    caplog,
) -> None:
    owner_store = RecordingOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    first_session_id = await manager.create_session()
    second_session_id = await manager.create_session()

    async def release_with_failure(
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        owner_store.release_calls.append(session_id)
        if session_id == first_session_id:
            raise RuntimeError("release failed")
        return await FakeOwnerStore.release(owner_store, session_id, owner_id, fencing_token)

    owner_store.release = release_with_failure

    await manager.release_owned_sessions()

    assert owner_store.release_calls == [first_session_id, second_session_id]
    assert "Failed to release owner lease" in caplog.text
    assert first_session_id in caplog.text
    assert second_session_id not in caplog.text


@pytest.mark.asyncio
async def test_renew_owner_leases_logs_and_continues_after_renew_exception(caplog) -> None:
    owner_store = RecordingOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    first_session_id = await manager.create_session()
    second_session_id = await manager.create_session()

    async def renew_with_failure(
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        owner_store.renew_calls.append(session_id)
        if session_id == first_session_id:
            raise RuntimeError("renew failed")
        return await FakeOwnerStore.renew(
            owner_store,
            session_id,
            owner_id,
            lease_seconds,
            new_fencing_token,
            current_fencing_token,
        )

    owner_store.renew = renew_with_failure

    await manager.renew_owner_leases()

    assert owner_store.renew_calls == [first_session_id, second_session_id]
    assert "Failed to renew owner lease" in caplog.text
    assert first_session_id in caplog.text
    assert second_session_id not in caplog.text


@pytest.mark.asyncio
async def test_close_session_releases_owner_lease_after_deleting_metadata() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=InMemorySessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    session_id = await manager.create_session()

    await manager.close_session(session_id)

    assert manager.list_sessions() == []
    assert await owner_store.get_owner(session_id) is None


@pytest.mark.asyncio
async def test_close_session_keeps_owner_lease_when_metadata_delete_fails() -> None:
    owner_store = FakeOwnerStore()
    manager = SessionManager(
        store=DeleteFailingSessionStore(),
        checkpoint_service=CheckpointService(FakeCheckpointStore()),
        owner_store=owner_store,
        owner_id="owner-a",
        fencing_token=7,
    )
    session_id = await manager.create_session()

    with pytest.raises(RuntimeError, match="session store delete failed"):
        await manager.close_session(session_id)

    owner = await owner_store.get_owner(session_id)
    assert owner is not None
    assert owner == SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=owner.lease_expires_at,
        fencing_token=7,
    )
