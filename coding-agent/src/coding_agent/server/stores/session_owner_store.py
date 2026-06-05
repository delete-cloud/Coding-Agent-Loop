from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
import sqlite3
import threading
from typing import Protocol

from agentkit.storage.pg import PGPool, PGSessionOwnerStore
from agentkit.tools import FatalToolExecutionError


class SessionOwnershipConflictReason(StrEnum):
    STALE_OWNER = "stale_owner"
    MISSING_OWNER = "missing_owner"
    EXPIRED_LEASE = "expired_lease"


class SessionOwnershipConflictError(FatalToolExecutionError):
    """Raised when the current instance is not authorized to mutate a session."""

    def __init__(
        self,
        message: str,
        *,
        reason: SessionOwnershipConflictReason = SessionOwnershipConflictReason.STALE_OWNER,
    ) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SessionOwnerRecord:
    owner_id: str
    lease_expires_at: datetime
    fencing_token: int


@dataclass(frozen=True)
class OwnerAuthority:
    session_id: str
    owner_id: str
    epoch: int

    @property
    def fencing_token(self) -> int:
        return self.epoch


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
    _SELECT_FOR_UPDATE_SQL = """
    SELECT owner_id, lease_expires_at, fencing_token
    FROM session_owners
    WHERE session_id = $1
    FOR UPDATE
    """
    _INSERT_AUTHORITY_SQL = """
    INSERT INTO session_owners (session_id, owner_id, lease_expires_at, fencing_token)
    VALUES ($1, $2, NOW() + make_interval(secs => $3), $4)
    ON CONFLICT (session_id) DO NOTHING
    """
    _UPDATE_AUTHORITY_SQL = """
    UPDATE session_owners
    SET owner_id = $2,
        lease_expires_at = NOW() + make_interval(secs => $3),
        fencing_token = $4,
        updated_at = NOW()
    WHERE session_id = $1
    """

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
        self._pg_pool = pg_pool

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

    async def acquire_authority(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
    ) -> OwnerAuthority:
        if self._pg_pool is None:
            raise TypeError("postgres owner authority requires pg_pool")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not owner_id:
            raise ValueError("owner_id must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        pool = await self._pg_pool.get_pool()
        _ = await pool.execute(PGSessionOwnerStore._CREATE_TABLE_SQL)
        connection = await self._pg_pool.acquire()
        try:
            _ = await connection.execute("BEGIN")
            _ = await connection.execute(
                self._INSERT_AUTHORITY_SQL,
                session_id,
                owner_id,
                lease_seconds,
                1,
            )
            row = await connection.fetchrow(self._SELECT_FOR_UPDATE_SQL, session_id)
            if row is None:
                raise RuntimeError("postgres owner insert returned no lockable row")
            row_dict = dict(row)
            current_owner_id = row_dict.get("owner_id")
            lease_expires_at = row_dict.get("lease_expires_at")
            current_epoch = row_dict.get("fencing_token")
            if not isinstance(current_owner_id, str):
                raise TypeError("postgres owner row missing string owner_id")
            if not isinstance(lease_expires_at, datetime):
                raise TypeError("postgres owner row missing datetime lease_expires_at")
            if not isinstance(current_epoch, int):
                raise TypeError("postgres owner row missing int fencing_token")
            now = datetime.now(
                lease_expires_at.tzinfo if lease_expires_at.tzinfo else UTC
            )
            if lease_expires_at > now:
                if current_owner_id != owner_id:
                    raise SessionOwnershipConflictError(
                        "stale owner or fencing token rejected"
                    )
                epoch = current_epoch
            else:
                epoch = current_epoch + 1
                _ = await connection.execute(
                    self._UPDATE_AUTHORITY_SQL,
                    session_id,
                    owner_id,
                    lease_seconds,
                    epoch,
                )
            _ = await connection.execute("COMMIT")
            return OwnerAuthority(session_id=session_id, owner_id=owner_id, epoch=epoch)
        except BaseException:
            try:
                _ = await connection.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            await self._pg_pool.release(connection)

    async def renew_authority(
        self,
        authority: OwnerAuthority,
        lease_seconds: float = 30.0,
    ) -> OwnerAuthority:
        renewed = await self.renew(
            authority.session_id,
            authority.owner_id,
            lease_seconds=lease_seconds,
            new_fencing_token=authority.epoch,
            current_fencing_token=authority.epoch,
        )
        if not renewed:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        return authority


class SQLiteSessionOwnerStore:
    _CREATE_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS session_owners (
        session_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        lease_expires_at TEXT NOT NULL,
        fencing_token INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(self._CREATE_SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    async def acquire(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
        fencing_token: int = 1,
    ) -> bool:
        _require_owner_input(
            session_id=session_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            fencing_token=fencing_token,
        )
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT owner_id, lease_expires_at, fencing_token
                FROM session_owners
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is not None:
                current_owner_id = _required_sqlite_str(row, "owner_id")
                current_expiry = _datetime_from_sqlite_text(row["lease_expires_at"])
                current_token = _required_sqlite_int(row, "fencing_token")
                if current_expiry > now:
                    return (
                        current_owner_id == owner_id and current_token == fencing_token
                    )
                if current_owner_id != owner_id and fencing_token <= current_token:
                    return False
                if current_owner_id == owner_id and fencing_token < current_token:
                    return False
            connection.execute(
                """
                INSERT INTO session_owners (
                    session_id, owner_id, lease_expires_at, fencing_token,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id)
                DO UPDATE SET
                    owner_id = excluded.owner_id,
                    lease_expires_at = excluded.lease_expires_at,
                    fencing_token = excluded.fencing_token,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    owner_id,
                    _datetime_to_sqlite_text(expires_at),
                    fencing_token,
                    _datetime_to_sqlite_text(now),
                    _datetime_to_sqlite_text(now),
                ),
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
        _require_owner_input(
            session_id=session_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            fencing_token=new_fencing_token,
        )
        if current_fencing_token <= 0:
            raise ValueError("current_fencing_token must be positive")
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE session_owners
                SET lease_expires_at = ?,
                    fencing_token = ?,
                    updated_at = ?
                WHERE session_id = ?
                  AND owner_id = ?
                  AND lease_expires_at > ?
                  AND fencing_token = ?
                """,
                (
                    _datetime_to_sqlite_text(expires_at),
                    new_fencing_token,
                    _datetime_to_sqlite_text(now),
                    session_id,
                    owner_id,
                    _datetime_to_sqlite_text(now),
                    current_fencing_token,
                ),
            )
        return cursor.rowcount == 1

    async def release(
        self,
        session_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> bool:
        _require_owner_input(
            session_id=session_id,
            owner_id=owner_id,
            lease_seconds=1.0,
            fencing_token=fencing_token,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM session_owners
                WHERE session_id = ?
                  AND owner_id = ?
                  AND fencing_token = ?
                """,
                (session_id, owner_id, fencing_token),
            )
        return cursor.rowcount == 1

    async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
        if not session_id.strip():
            raise ValueError("session_id must be non-empty")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT owner_id, lease_expires_at, fencing_token
                FROM session_owners
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionOwnerRecord(
            owner_id=_required_sqlite_str(row, "owner_id"),
            lease_expires_at=_datetime_from_sqlite_text(row["lease_expires_at"]),
            fencing_token=_required_sqlite_int(row, "fencing_token"),
        )

    async def acquire_authority(
        self,
        session_id: str,
        owner_id: str,
        lease_seconds: float = 30.0,
    ) -> OwnerAuthority:
        _require_owner_input(
            session_id=session_id,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            fencing_token=1,
        )
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT owner_id, lease_expires_at, fencing_token
                FROM session_owners
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                epoch = 1
            else:
                current_owner_id = _required_sqlite_str(row, "owner_id")
                current_expiry = _datetime_from_sqlite_text(row["lease_expires_at"])
                current_epoch = _required_sqlite_int(row, "fencing_token")
                if current_expiry > now and current_owner_id != owner_id:
                    raise SessionOwnershipConflictError(
                        "stale owner or fencing token rejected"
                    )
                if current_expiry > now:
                    epoch = current_epoch
                else:
                    epoch = current_epoch + 1
            connection.execute(
                """
                INSERT INTO session_owners (
                    session_id, owner_id, lease_expires_at, fencing_token,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id)
                DO UPDATE SET
                    owner_id = excluded.owner_id,
                    lease_expires_at = excluded.lease_expires_at,
                    fencing_token = excluded.fencing_token,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    owner_id,
                    _datetime_to_sqlite_text(expires_at),
                    epoch,
                    _datetime_to_sqlite_text(now),
                    _datetime_to_sqlite_text(now),
                ),
            )
        return OwnerAuthority(session_id=session_id, owner_id=owner_id, epoch=epoch)

    async def renew_authority(
        self,
        authority: OwnerAuthority,
        lease_seconds: float = 30.0,
    ) -> OwnerAuthority:
        _require_owner_input(
            session_id=authority.session_id,
            owner_id=authority.owner_id,
            lease_seconds=lease_seconds,
            fencing_token=authority.epoch,
        )
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE session_owners
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE session_id = ?
                  AND owner_id = ?
                  AND lease_expires_at > ?
                  AND fencing_token = ?
                """,
                (
                    _datetime_to_sqlite_text(expires_at),
                    _datetime_to_sqlite_text(now),
                    authority.session_id,
                    authority.owner_id,
                    _datetime_to_sqlite_text(now),
                    authority.epoch,
                ),
            )
        if cursor.rowcount != 1:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        return authority


def _require_owner_input(
    *,
    session_id: str,
    owner_id: str,
    lease_seconds: float,
    fencing_token: int,
) -> None:
    if not session_id.strip():
        raise ValueError("session_id must be non-empty")
    if not owner_id.strip():
        raise ValueError("owner_id must be non-empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if fencing_token <= 0:
        raise ValueError("fencing_token must be positive")


def _datetime_to_sqlite_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime_from_sqlite_text(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("sqlite session owner datetime must be text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_sqlite_str(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"sqlite session owner row missing string {key}")
    return value


def _required_sqlite_int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if not isinstance(value, int):
        raise TypeError(f"sqlite session owner row missing int {key}")
    return value
