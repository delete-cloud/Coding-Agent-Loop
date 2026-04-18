from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import json
import logging
import os
import threading
from collections.abc import Callable, Coroutine, Iterable
from typing import Final, Protocol, cast

from agentkit.storage.pg import PGPool

from ..redaction import redact_sensitive_text, redact_url_credentials

logger = logging.getLogger(__name__)

DEFAULT_REDIS_INDEX_KEY = "coding-agent:sessions:index"
DEFAULT_REDIS_KEY_PREFIX = "coding-agent:sessions"

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type SessionPayload = dict[str, JSONValue]


class RedisClient(Protocol):
    def ping(self) -> object: ...

    def set(self, key: str, value: str) -> object: ...

    def get(self, key: str) -> str | bytes | None: ...

    def delete(self, key: str) -> object: ...

    def sadd(self, key: str, value: str) -> object: ...

    def srem(self, key: str, value: str) -> object: ...

    def smembers(self, key: str) -> set[str] | set[bytes]: ...


class SessionStore(Protocol):
    def save(self, session_id: str, data: SessionPayload) -> None: ...

    def load(self, session_id: str) -> SessionPayload | None: ...

    def count_sessions(self) -> int: ...

    def list_sessions(self) -> list[str]: ...

    def delete(self, session_id: str) -> None: ...

    def check_health(self) -> bool: ...


class AsyncPGSessionPool(Protocol):
    async def get_pool(self) -> object: ...

    async def close(self) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionPayload] = {}

    def save(self, session_id: str, data: SessionPayload) -> None:
        self._sessions[session_id] = data

    def load(self, session_id: str) -> SessionPayload | None:
        return self._sessions.get(session_id)

    def count_sessions(self) -> int:
        return len(self._sessions)

    def get(self, session_id: str) -> SessionPayload | None:
        return self.load(session_id)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def delete(self, session_id: str) -> None:
        _ = self._sessions.pop(session_id, None)

    def check_health(self) -> bool:
        return True


class RedisSessionStore:
    def __init__(
        self,
        *,
        client: RedisClient,
        redis_url: str,
        key_prefix: str = DEFAULT_REDIS_KEY_PREFIX,
        index_key: str = DEFAULT_REDIS_INDEX_KEY,
    ) -> None:
        self._client: RedisClient = client
        self._redis_url: str = redis_url
        self._key_prefix: str = key_prefix
        self._index_key: str = index_key
        _ = self._client.ping()

    def _key_for(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}"

    def save(self, session_id: str, data: SessionPayload) -> None:
        payload = json.dumps(data)
        _ = self._client.set(self._key_for(session_id), payload)
        _ = self._client.sadd(self._index_key, session_id)

    def load(self, session_id: str) -> SessionPayload | None:
        raw = self._client.get(self._key_for(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data_obj = cast(object, json.loads(raw))
        if not isinstance(data_obj, dict):
            raise TypeError("redis session payload must be a JSON object")
        return cast(SessionPayload, data_obj)

    def list_sessions(self) -> list[str]:
        members = cast(set[str | bytes], self._client.smembers(self._index_key))
        session_ids: list[str] = []
        for member in members:
            if isinstance(member, bytes):
                session_ids.append(member.decode("utf-8"))
            else:
                session_ids.append(member)
        return sorted(session_ids)

    def count_sessions(self) -> int:
        return len(cast(set[str | bytes], self._client.smembers(self._index_key)))

    def delete(self, session_id: str) -> None:
        _ = self._client.delete(self._key_for(session_id))
        _ = self._client.srem(self._index_key, session_id)

    def check_health(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


class PGSessionMetadataStore:
    _CREATE_TABLE_SQL: Final[str] = """
    CREATE TABLE IF NOT EXISTS agent_http_sessions (
        session_id TEXT PRIMARY KEY,
        payload JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """
    _UPSERT_SQL: Final[str] = """
    INSERT INTO agent_http_sessions (session_id, payload)
    VALUES ($1, $2::jsonb)
    ON CONFLICT (session_id)
    DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
    """
    _SELECT_SQL: Final[str] = (
        "SELECT payload FROM agent_http_sessions WHERE session_id = $1"
    )
    _LIST_SQL: Final[str] = (
        "SELECT session_id FROM agent_http_sessions ORDER BY session_id"
    )
    _COUNT_SQL: Final[str] = "SELECT COUNT(*) AS session_count FROM agent_http_sessions"
    _DELETE_SQL: Final[str] = "DELETE FROM agent_http_sessions WHERE session_id = $1"
    _LOOP_READY_TIMEOUT_SECONDS: Final[float] = 5.0
    _SYNC_OPERATION_TIMEOUT_SECONDS: Final[float] = 30.0

    def __init__(
        self,
        *,
        pool: AsyncPGSessionPool,
    ) -> None:
        self._pool: AsyncPGSessionPool = pool
        self._schema_ready: bool = False
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._loop_ready: threading.Event = threading.Event()
        self._loop_thread: threading.Thread = threading.Thread(
            target=self._run_loop,
            name="pg-session-metadata-store",
            daemon=True,
        )
        self._loop_thread.start()
        ready = self._loop_ready.wait(self._LOOP_READY_TIMEOUT_SECONDS)
        if not ready:
            self._handle_loop_start_failure()
            raise RuntimeError("postgres session metadata loop thread failed to start")

    def _handle_loop_start_failure(self) -> None:
        try:
            _ = self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass
        self._loop_thread.join(timeout=0.1)
        if not self._loop_thread.is_alive():
            self._loop.close()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._loop_ready.set)
        self._loop.run_forever()

    def _run_sync(self, operation: Coroutine[object, object, object]) -> object:
        try:
            future = asyncio.run_coroutine_threadsafe(operation, self._loop)
        except RuntimeError as exc:
            operation.close()
            raise RuntimeError(
                "postgres session metadata loop is not available; "
                "the store may have been closed or failed to start"
            ) from exc
        try:
            return future.result(timeout=self._SYNC_OPERATION_TIMEOUT_SECONDS)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                "postgres session metadata operation timed out "
                f"after {self._SYNC_OPERATION_TIMEOUT_SECONDS} seconds"
            ) from exc

    async def _ensure_schema(self) -> object:
        asyncpg_pool = await self._pool.get_pool()
        if not self._schema_ready:
            execute = getattr(asyncpg_pool, "execute", None)
            if not callable(execute):
                raise TypeError("postgres session metadata pool must expose execute")
            _ = await cast(Callable[..., Coroutine[object, object, object]], execute)(
                self._CREATE_TABLE_SQL
            )
            self._schema_ready = True
        return asyncpg_pool

    def save(self, session_id: str, data: SessionPayload) -> None:
        async def _save() -> None:
            pool = await self._ensure_schema()
            execute = getattr(pool, "execute", None)
            if not callable(execute):
                raise TypeError("postgres session metadata pool must expose execute")
            _ = await cast(Callable[..., Coroutine[object, object, object]], execute)(
                self._UPSERT_SQL,
                session_id,
                data,
            )

        _ = self._run_sync(_save())

    def load(self, session_id: str) -> SessionPayload | None:
        async def _load() -> SessionPayload | None:
            pool = await self._ensure_schema()
            fetchrow = getattr(pool, "fetchrow", None)
            if not callable(fetchrow):
                raise TypeError("postgres session metadata pool must expose fetchrow")
            row_obj = await cast(
                Callable[..., Coroutine[object, object, object]], fetchrow
            )(
                self._SELECT_SQL,
                session_id,
            )
            if row_obj is None:
                return None
            row_dict = _coerce_row_dict(
                row=row_obj, context="postgres session metadata row"
            )
            payload = row_dict.get("payload")
            if payload is None:
                return None
            if not isinstance(payload, dict):
                raise TypeError(
                    "postgres session metadata payload must decode to a dict"
                )
            return cast(SessionPayload, payload)

        result = self._run_sync(_load())
        if result is None:
            return None
        if not isinstance(result, dict):
            raise TypeError("postgres session metadata payload must be a JSON object")
        return cast(SessionPayload, result)

    def list_sessions(self) -> list[str]:
        async def _list_sessions() -> list[str]:
            pool = await self._ensure_schema()
            fetch = getattr(pool, "fetch", None)
            if not callable(fetch):
                raise TypeError("postgres session metadata pool must expose fetch")
            rows_obj = await cast(
                Callable[..., Coroutine[object, object, object]], fetch
            )(self._LIST_SQL)
            if not isinstance(rows_obj, list):
                raise TypeError("postgres session metadata list result must be a list")
            session_ids: list[str] = []
            for row in cast(list[object], rows_obj):
                row_dict = _coerce_row_dict(
                    row=row, context="postgres session metadata list row"
                )
                session_id = row_dict.get("session_id")
                if not isinstance(session_id, str):
                    raise TypeError(
                        "postgres session metadata row must include string session_id"
                    )
                session_ids.append(session_id)
            return session_ids

        result = self._run_sync(_list_sessions())
        if not isinstance(result, list):
            raise TypeError("postgres session metadata list result must be a list")
        return cast(list[str], result)

    def count_sessions(self) -> int:
        async def _count_sessions() -> int:
            pool = await self._ensure_schema()
            fetchrow = getattr(pool, "fetchrow", None)
            if not callable(fetchrow):
                raise TypeError("postgres session metadata pool must expose fetchrow")
            row_obj = await cast(
                Callable[..., Coroutine[object, object, object]], fetchrow
            )(self._COUNT_SQL)
            if row_obj is None:
                raise TypeError(
                    "postgres session metadata count result must not be None"
                )
            row_dict = _coerce_row_dict(
                row=row_obj, context="postgres session metadata count row"
            )
            session_count = row_dict.get("session_count")
            if not isinstance(session_count, int):
                raise TypeError(
                    "postgres session metadata count row must include int session_count"
                )
            return session_count

        result = self._run_sync(_count_sessions())
        if not isinstance(result, int):
            raise TypeError("postgres session metadata count result must be an int")
        return result

    def delete(self, session_id: str) -> None:
        async def _delete() -> None:
            pool = await self._ensure_schema()
            execute = getattr(pool, "execute", None)
            if not callable(execute):
                raise TypeError("postgres session metadata pool must expose execute")
            _ = await cast(Callable[..., Coroutine[object, object, object]], execute)(
                self._DELETE_SQL,
                session_id,
            )

        _ = self._run_sync(_delete())

    def check_health(self) -> bool:
        try:

            async def _check_health() -> bool:
                pool = await self._ensure_schema()
                fetchrow = getattr(pool, "fetchrow", None)
                if not callable(fetchrow):
                    raise TypeError(
                        "postgres session metadata pool must expose fetchrow"
                    )
                row_obj = await cast(
                    Callable[..., Coroutine[object, object, object]], fetchrow
                )("SELECT 1")
                return row_obj is not None

            return bool(self._run_sync(_check_health()))
        except Exception:
            return False

    def close(self) -> None:
        async def _close_pool() -> None:
            await self._pool.close()

        try:
            try:
                _ = self._run_sync(_close_pool())
            except Exception:
                logger.warning(
                    "Failed to close postgres session metadata pool during shutdown",
                    exc_info=True,
                )
        finally:
            try:
                _ = self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                logger.warning(
                    "Postgres session metadata loop was already closed while "
                    "scheduling stop during shutdown"
                )
            self._loop_thread.join(timeout=5)
            if self._loop_thread.is_alive():
                logger.warning(
                    "Timed out waiting for postgres session metadata loop thread to stop; skipping event loop close"
                )
            else:
                try:
                    self._loop.close()
                except RuntimeError:
                    logger.warning(
                        "Postgres session metadata loop was already closed during shutdown"
                    )


def create_session_store(
    *,
    backend: str | None = None,
    dsn: str | None = None,
    redis_url: str | None = None,
    redis_client_factory: Callable[[str], RedisClient] | None = None,
    pg_pool: AsyncPGSessionPool | None = None,
) -> SessionStore:
    resolved_backend = (
        (backend or os.environ.get("AGENT_SESSION_BACKEND") or "").strip().lower()
    )
    resolved_dsn = (
        dsn
        or os.environ.get("AGENT_SESSION_PG_DSN")
        or os.environ.get("AGENT_STORAGE_DSN")
    )
    normalized_dsn = resolved_dsn.strip() if isinstance(resolved_dsn, str) else None
    resolved_redis_url = redis_url or os.environ.get("AGENT_SESSION_REDIS_URL")
    if resolved_backend == "pg":
        if pg_pool is None and not normalized_dsn:
            raise ValueError("PG session store requires dsn or pg_pool")
        if pg_pool is None:
            assert normalized_dsn is not None
            pg_pool = PGPool(dsn=normalized_dsn)
        return PGSessionMetadataStore(
            pool=pg_pool,
        )

    if resolved_backend == "memory":
        return InMemorySessionStore()

    if resolved_backend not in {"", "redis"}:
        raise ValueError(f"unsupported session store backend: {resolved_backend}")

    if not resolved_redis_url:
        return InMemorySessionStore()

    try:
        client = _create_redis_client(
            redis_url=resolved_redis_url,
            redis_client_factory=redis_client_factory,
        )
        return RedisSessionStore(client=client, redis_url=resolved_redis_url)
    except Exception as exc:
        safe_exc = redact_sensitive_text(str(exc))
        logger.warning(
            "Redis session store unavailable at %s; falling back to in-memory store: %s",
            redact_url_credentials(resolved_redis_url),
            safe_exc,
        )
        return InMemorySessionStore()


def _coerce_row_dict(*, row: object, context: str) -> dict[str, object]:
    if isinstance(row, dict):
        return row
    if not isinstance(row, Iterable):
        raise TypeError(f"{context} must be convertible to a dict")
    try:
        row_items = cast(Iterable[tuple[object, object]], row)
        row_dict_obj = dict(row_items)
    except Exception as exc:
        raise TypeError(f"{context} must be convertible to a dict") from exc
    if not isinstance(row_dict_obj, dict):
        raise TypeError(f"{context} must decode to a dict")
    return cast(dict[str, object], row_dict_obj)


def _create_redis_client(
    *,
    redis_url: str,
    redis_client_factory: Callable[[str], RedisClient] | None,
) -> RedisClient:
    if redis_client_factory is not None:
        return redis_client_factory(redis_url)

    import importlib

    redis_module = importlib.import_module("redis")
    module_factory = cast(
        Callable[..., RedisClient] | None,
        getattr(redis_module, "from_url", None),
    )
    if callable(module_factory):
        return module_factory(redis_url, decode_responses=True)

    redis_cls_obj: object = getattr(redis_module, "Redis", None)
    if redis_cls_obj is None:
        raise ImportError("redis package does not expose Redis client")
    class_factory = cast(
        Callable[..., RedisClient] | None,
        getattr(redis_cls_obj, "from_url", None),
    )
    if not callable(class_factory):
        raise ImportError("redis package does not expose Redis.from_url")
    return class_factory(redis_url, decode_responses=True)
