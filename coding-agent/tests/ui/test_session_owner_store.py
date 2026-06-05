from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest

from coding_agent.server.stores.session_owner_store import (
    SessionOwnerRecord,
    SQLiteSessionOwnerStore,
    SessionOwnerStore,
)


class FakePGOwnerStore:
    def __init__(self) -> None:
        self._owners: dict[str, SessionOwnerRecord] = {}

    async def acquire(
        self, session_id: str, owner_id: str, lease_seconds: float, fencing_token: int
    ) -> bool:
        owner = self._owners.get(session_id)
        now = datetime.now(UTC)
        if owner is not None and owner.lease_expires_at > now:
            return False
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            fencing_token=fencing_token,
        )
        return True

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float,
        new_fencing_token: int,
        current_fencing_token: int,
    ) -> bool:
        owner = self._owners.get(session_id)
        now = datetime.now(UTC)
        if owner is None or owner.lease_expires_at <= now:
            return False
        if owner.owner_id != owner_id or owner.fencing_token != current_fencing_token:
            return False
        self._owners[session_id] = SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            fencing_token=new_fencing_token,
        )
        return True

    async def release(self, session_id: str, owner_id: str, fencing_token: int) -> bool:
        owner = self._owners.get(session_id)
        if owner is None:
            return False
        if owner.owner_id != owner_id or owner.fencing_token != fencing_token:
            return False
        del self._owners[session_id]
        return True

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        return self._owners.get(session_id)


@pytest.fixture
def owner_store() -> SessionOwnerStore:
    return SessionOwnerStore(pg_store=FakePGOwnerStore())


@pytest.mark.asyncio
async def test_session_owner_acquire_conflicts_with_live_lease(
    owner_store: SessionOwnerStore,
) -> None:
    assert await owner_store.acquire(
        "s1", "owner-a", lease_seconds=30.0, fencing_token=1
    )
    assert not await owner_store.acquire(
        "s1", "owner-b", lease_seconds=30.0, fencing_token=2
    )


@pytest.mark.asyncio
async def test_session_owner_renew_rejects_stale_token(
    owner_store: SessionOwnerStore,
) -> None:
    await owner_store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)

    assert not await owner_store.renew(
        "s1",
        "owner-a",
        lease_seconds=30.0,
        new_fencing_token=3,
        current_fencing_token=99,
    )


@pytest.mark.asyncio
async def test_session_owner_release_rejects_wrong_owner(
    owner_store: SessionOwnerStore,
) -> None:
    await owner_store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)

    assert not await owner_store.release("s1", "owner-b", fencing_token=1)


@pytest.mark.asyncio
async def test_session_owner_get_owner_returns_typed_record(
    owner_store: SessionOwnerStore,
) -> None:
    await owner_store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)

    owner = await owner_store.get_owner("s1")
    assert owner is not None

    assert owner == SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=owner.lease_expires_at,
        fencing_token=1,
    )


@pytest.mark.asyncio
async def test_sqlite_session_owner_store_acquire_renew_release_and_takeover(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionOwnerStore(tmp_path / "owners.sqlite3")

    assert await store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)
    assert not await store.acquire(
        "s1",
        "owner-b",
        lease_seconds=30.0,
        fencing_token=2,
    )

    renewed = await store.renew(
        "s1",
        "owner-a",
        lease_seconds=30.0,
        new_fencing_token=2,
        current_fencing_token=1,
    )
    owner = await store.get_owner("s1")

    assert renewed is True
    assert owner is not None
    assert owner.owner_id == "owner-a"
    assert owner.fencing_token == 2

    assert await store.release("s1", "owner-a", fencing_token=2)
    assert await store.get_owner("s1") is None

    assert await store.acquire(
        "s1",
        "owner-dead",
        lease_seconds=0.001,
        fencing_token=3,
    )
    await asyncio.sleep(0.01)

    assert await store.acquire("s1", "owner-b", lease_seconds=30.0, fencing_token=4)
    taken = await store.get_owner("s1")

    assert taken == SessionOwnerRecord(
        owner_id="owner-b",
        lease_expires_at=taken.lease_expires_at,
        fencing_token=4,
    )


@pytest.mark.asyncio
async def test_sqlite_session_owner_store_rejects_stale_owner(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionOwnerStore(tmp_path / "owners.sqlite3")
    assert await store.acquire("s1", "owner-a", lease_seconds=30.0, fencing_token=1)

    assert not await store.renew(
        "s1",
        "owner-a",
        lease_seconds=30.0,
        new_fencing_token=2,
        current_fencing_token=99,
    )
    assert not await store.release("s1", "owner-b", fencing_token=1)

    owner = await store.get_owner("s1")

    assert owner == SessionOwnerRecord(
        owner_id="owner-a",
        lease_expires_at=owner.lease_expires_at,
        fencing_token=1,
    )


@pytest.mark.asyncio
async def test_sqlite_session_owner_store_rejects_non_increasing_takeover_token(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionOwnerStore(tmp_path / "owners.sqlite3")
    assert await store.acquire(
        "s1",
        "owner-dead",
        lease_seconds=0.001,
        fencing_token=10,
    )
    await asyncio.sleep(0.01)

    assert not await store.acquire(
        "s1",
        "owner-lower",
        lease_seconds=30.0,
        fencing_token=7,
    )

    owner = await store.get_owner("s1")
    assert owner == SessionOwnerRecord(
        owner_id="owner-dead",
        lease_expires_at=owner.lease_expires_at,
        fencing_token=10,
    )

    assert await store.acquire(
        "s1",
        "owner-next",
        lease_seconds=30.0,
        fencing_token=11,
    )
    taken = await store.get_owner("s1")
    assert taken == SessionOwnerRecord(
        owner_id="owner-next",
        lease_expires_at=taken.lease_expires_at,
        fencing_token=11,
    )
