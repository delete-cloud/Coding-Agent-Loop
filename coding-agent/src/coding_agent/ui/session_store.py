# pyright: reportMissingTypeStubs=false

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Protocol, cast

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

    def list_sessions(self) -> list[str]: ...

    def delete(self, session_id: str) -> None: ...

    def check_health(self) -> bool: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionPayload] = {}

    def save(self, session_id: str, data: SessionPayload) -> None:
        self._sessions[session_id] = data

    def load(self, session_id: str) -> SessionPayload | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def delete(self, session_id: str) -> None:
        _ = self._sessions.pop(session_id, None)

    def get(self, session_id: str) -> SessionPayload | None:
        return self.load(session_id)

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

    def delete(self, session_id: str) -> None:
        _ = self._client.delete(self._key_for(session_id))
        _ = self._client.srem(self._index_key, session_id)

    def check_health(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


def create_session_store(
    *,
    redis_url: str | None = None,
    redis_client_factory: Callable[[str], RedisClient] | None = None,
) -> SessionStore:
    resolved_redis_url = redis_url or os.environ.get("AGENT_SESSION_REDIS_URL")
    if not resolved_redis_url:
        return InMemorySessionStore()

    try:
        client = _create_redis_client(
            redis_url=resolved_redis_url,
            redis_client_factory=redis_client_factory,
        )
        return RedisSessionStore(client=client, redis_url=resolved_redis_url)
    except Exception as exc:
        logger.warning(
            "Redis session store unavailable at %s; falling back to in-memory store: %s",
            resolved_redis_url,
            exc,
        )
        return InMemorySessionStore()


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
