"""Inbound runtime-message bus primitives.

Each consumer owns its own cursor. Product approval stores should consume
``approval_decision`` messages with a cursor separate from the legacy pipeline
cursor. ``approval_decision`` payloads should use ``{"request_id": str,
"approved": bool}`` plus product-specific routing fields. ``subagent_message``
payloads should use ``{"text": str}``.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("runtime message mapping keys must be strings")
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, tuple | list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("runtime message values must contain finite floats")
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError("runtime message values must contain host-neutral JSON values")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("payload must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TypeError("payload keys must be strings")
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


class RuntimeMessageKind(StrEnum):
    """Inbound runtime controls consumed by legacy pipeline safe points."""

    INTERRUPT = "interrupt"
    USER_STEER = "user_steer"
    APPROVAL_DECISION = "approval_decision"
    SUBAGENT_MESSAGE = "subagent_message"
    SYSTEM_NOTICE = "system_notice"


class DuplicateRuntimeMessageError(ValueError):
    """Raised when a runtime bus rejects an already-published message ID."""

    def __init__(self, message_id: str) -> None:
        super().__init__(f"duplicate runtime message_id: {message_id}")
        self.message_id = message_id


def _coerce_runtime_message_kind(kind: RuntimeMessageKind | str) -> RuntimeMessageKind:
    try:
        return RuntimeMessageKind(kind)
    except ValueError as exc:
        raise ValueError(f"unknown runtime message kind: {kind}") from exc


def _normalize_runtime_message_kinds(
    kinds: Iterable[RuntimeMessageKind | str] | RuntimeMessageKind | str | None,
) -> frozenset[RuntimeMessageKind] | None:
    if kinds is None:
        return None
    if isinstance(kinds, str):
        kinds = (kinds,)

    normalized = frozenset(_coerce_runtime_message_kind(kind) for kind in kinds)
    if not normalized:
        raise ValueError("kinds must be non-empty when provided")
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimeMessage:
    """A runtime control message before it is assigned a bus sequence."""

    message_id: str
    kind: RuntimeMessageKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.message_id:
            raise ValueError("message_id must be non-empty")
        if not isinstance(self.kind, RuntimeMessageKind):
            object.__setattr__(
                self,
                "kind",
                _coerce_runtime_message_kind(self.kind),
            )
        if isinstance(self.created_at, bool) or not isinstance(
            self.created_at,
            int | float,
        ):
            raise TypeError("created_at must be a number")
        if isinstance(self.created_at, float) and not math.isfinite(self.created_at):
            raise ValueError("created_at must be finite")
        if self.created_at < 0:
            raise ValueError("created_at must be non-negative")
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class RuntimeMessageCursor:
    """Idempotent cursor for consuming messages after a sequence number."""

    sequence: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")


@dataclass(frozen=True, slots=True)
class SequencedRuntimeMessage:
    """Runtime message after durable sequencing by a bus."""

    sequence: int
    message: RuntimeMessage

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        if not isinstance(self.message, RuntimeMessage):
            raise TypeError("message must be a RuntimeMessage")


@dataclass(frozen=True, slots=True)
class RuntimeMessageBatch:
    """Messages returned for a cursor plus the next cursor to persist."""

    messages: tuple[SequencedRuntimeMessage, ...]
    cursor: RuntimeMessageCursor

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if any(not isinstance(item, SequencedRuntimeMessage) for item in messages):
            raise TypeError("messages must contain SequencedRuntimeMessage values")
        if not isinstance(self.cursor, RuntimeMessageCursor):
            raise TypeError("cursor must be a RuntimeMessageCursor")
        object.__setattr__(self, "messages", messages)


class RuntimeMessageBus(Protocol):
    """Inbound message bus with caller-owned idempotent cursor advancement."""

    async def publish(self, message: RuntimeMessage) -> SequencedRuntimeMessage: ...

    async def consume_after(
        self,
        cursor: RuntimeMessageCursor,
        *,
        kinds: Iterable[RuntimeMessageKind | str] | None = None,
        limit: int | None = None,
    ) -> RuntimeMessageBatch: ...


class InMemoryRuntimeMessageBus:
    """Process-local inbound bus for tests and legacy runtime wiring."""

    def __init__(self) -> None:
        self._messages: list[SequencedRuntimeMessage] = []
        self._message_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def publish(self, message: RuntimeMessage) -> SequencedRuntimeMessage:
        if not isinstance(message, RuntimeMessage):
            raise TypeError("message must be a RuntimeMessage")
        async with self._lock:
            if message.message_id in self._message_ids:
                raise DuplicateRuntimeMessageError(message.message_id)
            sequenced = SequencedRuntimeMessage(
                sequence=len(self._messages) + 1,
                message=message,
            )
            self._messages.append(sequenced)
            self._message_ids.add(message.message_id)
            return sequenced

    async def consume_after(
        self,
        cursor: RuntimeMessageCursor,
        *,
        kinds: Iterable[RuntimeMessageKind | str] | None = None,
        limit: int | None = None,
    ) -> RuntimeMessageBatch:
        if not isinstance(cursor, RuntimeMessageCursor):
            raise TypeError("cursor must be a RuntimeMessageCursor")
        normalized_kinds = _normalize_runtime_message_kinds(kinds)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit must be an integer or None")
            if limit <= 0:
                raise ValueError("limit must be positive")

        async with self._lock:
            messages = [
                item for item in self._messages if item.sequence > cursor.sequence
            ]
            if normalized_kinds is not None:
                messages = [
                    item for item in messages if item.message.kind in normalized_kinds
                ]
            if limit is not None:
                messages = messages[:limit]
            next_cursor = (
                RuntimeMessageCursor(messages[-1].sequence) if messages else cursor
            )
            return RuntimeMessageBatch(messages=tuple(messages), cursor=next_cursor)
