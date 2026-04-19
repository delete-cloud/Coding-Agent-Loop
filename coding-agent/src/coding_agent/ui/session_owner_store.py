from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agentkit.storage.pg import PGPool, PGSessionOwnerStore


@dataclass(frozen=True)
class SessionOwnerRecord:
    owner_id: str
    lease_expires_at: datetime
    fencing_token: int


class SessionOwnerBackend(Protocol):
    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float,
        fencing_token: int,
    ) -> bool: ...

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float,
        new_fencing_token: int,
        current_fencing_token: int,
    ) -> bool: ...

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool: ...

    async def get_owner(
        self, session_id: str
    ) -> dict[str, object] | SessionOwnerRecord | None: ...


class SessionOwnerStoreProtocol(Protocol):
    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool: ...

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool: ...

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool: ...

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None: ...


class SessionOwnerStore:
    def __init__(
        self,
        *,
        pg_pool: PGPool | None = None,
        pg_store: SessionOwnerBackend | None = None,
    ) -> None:
        if pg_store is None:
            if pg_pool is None:
                raise ValueError("SessionOwnerStore requires pg_pool or pg_store")
            pg_store = PGSessionOwnerStore(pool=pg_pool)
        self._pg: SessionOwnerBackend = pg_store

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        return await self._pg.acquire(
            session_id,
            owner_id,
            lease_seconds,
            fencing_token,
        )

    async def renew(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        new_fencing_token: int = 2,
        current_fencing_token: int = 1,
    ) -> bool:
        return await self._pg.renew(
            session_id,
            owner_id,
            lease_seconds,
            new_fencing_token,
            current_fencing_token,
        )

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        return await self._pg.release(session_id, owner_id, fencing_token)

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        raw = await self._pg.get_owner(session_id)
        if raw is None:
            return None
        if isinstance(raw, SessionOwnerRecord):
            return raw

        owner_id = raw.get("owner_id")
        lease_expires_at = raw.get("lease_expires_at")
        fencing_token = raw.get("fencing_token")
        if not isinstance(owner_id, str):
            raise TypeError("session owner payload missing string owner_id")
        if not isinstance(lease_expires_at, datetime):
            raise TypeError("session owner payload missing datetime lease_expires_at")
        if not isinstance(fencing_token, int):
            raise TypeError("session owner payload missing int fencing_token")

        return SessionOwnerRecord(
            owner_id=owner_id,
            lease_expires_at=lease_expires_at,
            fencing_token=fencing_token,
        )
