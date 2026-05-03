"""Inbound runtime message primitives."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


class RuntimeMessageKind(StrEnum):
    """Inbound runtime controls consumed by pipeline safe points."""

    INTERRUPT = "interrupt"
    USER_STEER = "user_steer"
    APPROVAL_DECISION = "approval_decision"
    SUBAGENT_MESSAGE = "subagent_message"
    SYSTEM_NOTICE = "system_notice"


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
            object.__setattr__(self, "kind", RuntimeMessageKind(self.kind))
        if isinstance(self.created_at, bool) or not isinstance(
            self.created_at, int | float
        ):
            raise TypeError("created_at must be a number")
        if self.created_at < 0:
            raise ValueError("created_at must be non-negative")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


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


@dataclass(frozen=True, slots=True)
class RuntimeMessageBatch:
    """Messages returned for a cursor plus the next cursor to persist."""

    messages: tuple[SequencedRuntimeMessage, ...]
    cursor: RuntimeMessageCursor


class RuntimeMessageBus(Protocol):
    """Inbound message bus with caller-owned idempotent cursor advancement."""

    async def publish(self, message: RuntimeMessage) -> SequencedRuntimeMessage: ...

    async def consume_after(
        self,
        cursor: RuntimeMessageCursor,
        *,
        limit: int | None = None,
    ) -> RuntimeMessageBatch: ...


class InMemoryRuntimeMessageBus:
    """Process-local runtime message bus for tests and single-runtime wiring."""

    def __init__(self) -> None:
        self._messages: list[SequencedRuntimeMessage] = []
        self._message_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def publish(self, message: RuntimeMessage) -> SequencedRuntimeMessage:
        async with self._lock:
            if message.message_id in self._message_ids:
                raise ValueError(f"duplicate runtime message_id: {message.message_id}")
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
        limit: int | None = None,
    ) -> RuntimeMessageBatch:
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit must be an integer or None")
            if limit <= 0:
                raise ValueError("limit must be positive")

        async with self._lock:
            messages = [
                item for item in self._messages if item.sequence > cursor.sequence
            ]
            if limit is not None:
                messages = messages[:limit]
            next_cursor = (
                RuntimeMessageCursor(messages[-1].sequence) if messages else cursor
            )
            return RuntimeMessageBatch(
                messages=tuple(messages),
                cursor=next_cursor,
            )
