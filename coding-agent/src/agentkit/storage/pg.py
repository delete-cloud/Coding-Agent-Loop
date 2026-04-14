from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Awaitable, Callable
from typing import Final, Protocol, cast


class AsyncPGPool(Protocol):
    async def execute(self, query: str, *args: object) -> str: ...

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None: ...

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]: ...

    async def acquire(self) -> LockConnection: ...

    async def release(self, connection: LockConnection) -> None: ...

    async def close(self) -> None: ...


class LockConnection(Protocol):
    async def execute(self, query: str, *args: object) -> object: ...


class LockPool(Protocol):
    async def acquire(self) -> LockConnection: ...

    async def release(self, connection: LockConnection) -> None: ...


PoolFactory = Callable[..., Awaitable[AsyncPGPool]]


class PGPool:
    def __init__(
        self,
        *,
        dsn: str,
        min_size: int = 1,
        max_size: int = 10,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        self._dsn: str = dsn
        self._min_size: int = min_size
        self._max_size: int = max_size
        self._pool_factory: PoolFactory | None = pool_factory
        self._pool: AsyncPGPool | None = None
        self._pool_lock: asyncio.Lock = asyncio.Lock()

    async def get_pool(self) -> AsyncPGPool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    factory = self._pool_factory or _load_asyncpg_pool_factory()
                    self._pool = await factory(
                        dsn=self._dsn,
                        min_size=self._min_size,
                        max_size=self._max_size,
                    )
        return self._pool

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    async def acquire(self) -> LockConnection:
        pool = await self.get_pool()
        return await pool.acquire()

    async def release(self, connection: LockConnection) -> None:
        pool = await self.get_pool()
        await pool.release(connection)


class PGSessionStore:
    _CREATE_TABLE_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS agent_sessions (
        session_id TEXT PRIMARY KEY,
        payload JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """

    _UPSERT_SQL: Final[str] = """
    INSERT INTO agent_sessions (session_id, payload)
    VALUES ($1, $2::jsonb)
    ON CONFLICT (session_id)
    DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
    """

    _SELECT_SQL: Final[str] = "SELECT payload FROM agent_sessions WHERE session_id = $1"
    _LIST_SQL: Final[str] = "SELECT session_id FROM agent_sessions ORDER BY session_id"
    _DELETE_SQL: Final[str] = "DELETE FROM agent_sessions WHERE session_id = $1"

    def __init__(self, *, pool: PGPool) -> None:
        self._pool: PGPool = pool
        self._schema_ready: bool = False

    async def _ensure_schema(self) -> AsyncPGPool:
        pool = await self._pool.get_pool()
        if not self._schema_ready:
            _ = await pool.execute(self._CREATE_TABLE_SQL)
            self._schema_ready = True
        return pool

    async def save_session(self, session_id: str, data: dict[str, object]) -> None:
        pool = await self._ensure_schema()
        _ = await pool.execute(self._UPSERT_SQL, session_id, json.dumps(data))

    async def load_session(self, session_id: str) -> dict[str, object] | None:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._SELECT_SQL, session_id)
        if row is None:
            return None

        payload = row.get("payload")
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise TypeError("postgres session payload must decode to a dict")
        return cast(dict[str, object], payload)

    async def list_sessions(self) -> list[str]:
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_SQL)
        session_ids: list[str] = []
        for row in rows:
            session_id = row.get("session_id")
            if not isinstance(session_id, str):
                raise TypeError("postgres session row must include string session_id")
            session_ids.append(session_id)
        return session_ids

    async def delete_session(self, session_id: str) -> None:
        pool = await self._ensure_schema()
        _ = await pool.execute(self._DELETE_SQL, session_id)


class PGTapeStore:
    _CREATE_TABLE_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS agent_tapes (
        tape_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        entry JSONB NOT NULL,
        PRIMARY KEY (tape_id, seq)
    )
    """
    _MAX_SEQ_SQL: Final[str] = (
        "SELECT MAX(seq) AS max_seq FROM agent_tapes WHERE tape_id = $1"
    )
    _INSERT_SQL: Final[str] = """
    INSERT INTO agent_tapes (tape_id, seq, entry)
    SELECT $1, seq, entry::jsonb
    FROM unnest($2::int[], $3::text[]) AS batch(seq, entry)
    """
    _LOAD_SQL: Final[str] = (
        "SELECT entry FROM agent_tapes WHERE tape_id = $1 ORDER BY seq"
    )
    _LIST_SQL: Final[str] = "SELECT DISTINCT tape_id FROM agent_tapes ORDER BY tape_id"
    _TRUNCATE_SQL: Final[str] = (
        "DELETE FROM agent_tapes WHERE tape_id = $1 AND seq >= $2"
    )

    def __init__(self, *, pool: PGPool) -> None:
        self._pool: PGPool = pool
        self._schema_ready: bool = False

    async def _ensure_schema(self) -> AsyncPGPool:
        pool = await self._pool.get_pool()
        if not self._schema_ready:
            _ = await pool.execute(self._CREATE_TABLE_SQL)
            self._schema_ready = True
        return pool

    async def save(self, tape_id: str, entries: list[dict[str, object]]) -> None:
        if not entries:
            return

        pool = await self._ensure_schema()
        row = await pool.fetchrow(self._MAX_SEQ_SQL, tape_id)
        max_seq = -1
        if row is not None:
            raw_max_seq = row.get("max_seq")
            if raw_max_seq is None:
                max_seq = -1
            elif isinstance(raw_max_seq, int):
                max_seq = raw_max_seq
            else:
                raise TypeError("postgres tape row must include int max_seq or None")

        seq_values = [max_seq + offset for offset, _ in enumerate(entries, start=1)]
        payload_values = [json.dumps(entry) for entry in entries]
        _ = await pool.execute(
            self._INSERT_SQL,
            tape_id,
            seq_values,
            payload_values,
        )

    async def load(self, tape_id: str) -> list[dict[str, object]]:
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LOAD_SQL, tape_id)
        loaded: list[dict[str, object]] = []
        for row in rows:
            entry = row.get("entry")
            if not isinstance(entry, dict):
                raise TypeError("postgres tape row must include dict entry")
            loaded.append(cast(dict[str, object], entry))
        return loaded

    async def list_ids(self) -> list[str]:
        pool = await self._ensure_schema()
        rows = await pool.fetch(self._LIST_SQL)
        tape_ids: list[str] = []
        for row in rows:
            tape_id = row.get("tape_id")
            if not isinstance(tape_id, str):
                raise TypeError("postgres tape row must include string tape_id")
            tape_ids.append(tape_id)
        return tape_ids

    async def truncate(self, tape_id: str, keep: int) -> None:
        if keep < 0:
            raise ValueError("keep must be >= 0")
        pool = await self._ensure_schema()
        _ = await pool.execute(self._TRUNCATE_SQL, tape_id, keep)


class PGSessionLock:
    def __init__(self, *, pool: LockPool) -> None:
        self._pool: LockPool = pool
        self._conn: LockConnection | None = None

    async def acquire(self, session_id: str) -> None:
        if self._conn is not None:
            raise RuntimeError("PGSessionLock.acquire() called while already held")

        conn = await self._pool.acquire()
        try:
            _ = await conn.execute("SELECT pg_advisory_lock(hashtext($1))", session_id)
        except BaseException:
            await self._pool.release(conn)
            raise
        self._conn = conn

    async def release(self) -> None:
        if self._conn is None:
            return
        try:
            _ = await self._conn.execute("SELECT pg_advisory_unlock_all()")
        finally:
            await self._pool.release(self._conn)
            self._conn = None


def _load_asyncpg_pool_factory() -> PoolFactory:
    try:
        asyncpg_module = importlib.import_module("asyncpg")
    except ModuleNotFoundError as exc:
        raise ImportError(
            "asyncpg is required for PostgreSQL storage backends"
        ) from exc

    create_pool = getattr(asyncpg_module, "create_pool", None)
    if not callable(create_pool):
        raise ImportError("asyncpg does not expose create_pool")
    return cast(PoolFactory, create_pool)
