"""Inbound runtime message primitives.

Each consumer owns its own cursor. Product approval stores should consume
``approval_decision`` messages with a cursor separate from the agentkit pipeline
cursor. ``approval_decision`` payloads should use ``{"request_id": str,
"approved": bool}`` plus product-specific routing fields. ``subagent_message``
payloads should use ``{"text": str}``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Mapping
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
        kinds: Iterable[RuntimeMessageKind | str] | None = None,
        limit: int | None = None,
    ) -> RuntimeMessageBatch: ...


class InMemoryRuntimeMessageBus:
    """Process-local runtime message bus for tests and single-runtime wiring.

    The bus keeps all messages and message IDs in memory, so it is not suitable
    for long-running durable processes.
    """

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
        kinds: Iterable[RuntimeMessageKind | str] | None = None,
        limit: int | None = None,
    ) -> RuntimeMessageBatch:
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
            return RuntimeMessageBatch(
                messages=tuple(messages),
                cursor=next_cursor,
            )
