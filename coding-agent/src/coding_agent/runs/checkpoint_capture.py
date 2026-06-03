from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.checkpoint.models import CheckpointMeta

from .checkpoint_restore import (
    CHECKPOINT_SESSION_CONFIG_KEY,
    CheckpointRestoreSession,
    serialize_checkpoint_session_config,
)


class RuntimeCheckpointCaptureBackend(Protocol):
    async def capture(
        self,
        ctx: Any,
        *,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta: ...


class RuntimeCheckpointCaptureContextTape(Protocol):
    tape_id: str


class RuntimeCheckpointCaptureContext(Protocol):
    tape: RuntimeCheckpointCaptureContextTape


RuntimeCheckpointCaptureSession = CheckpointRestoreSession
RuntimeCheckpointCaptureBackendProvider = Callable[
    [],
    RuntimeCheckpointCaptureBackend,
]
RuntimeCheckpointRuntimeEnsurer = Callable[
    [str],
    Awaitable[RuntimeCheckpointCaptureContext],
]
RuntimeCheckpointCapturePersister = Callable[
    [RuntimeCheckpointCaptureSession],
    Awaitable[None],
]


@dataclass(frozen=True)
class RuntimeCheckpointCaptureService:
    checkpoint_service: RuntimeCheckpointCaptureBackendProvider
    ensure_runtime: RuntimeCheckpointRuntimeEnsurer
    persist_session: RuntimeCheckpointCapturePersister

    async def capture(
        self,
        session: RuntimeCheckpointCaptureSession,
        *,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        ctx = await self.ensure_runtime(session.id)
        payload = dict(extra or {})
        if CHECKPOINT_SESSION_CONFIG_KEY in payload:
            raise ValueError(
                f"'{CHECKPOINT_SESSION_CONFIG_KEY}' is a reserved checkpoint metadata key and cannot be provided via extra"
            )
        payload[CHECKPOINT_SESSION_CONFIG_KEY] = serialize_checkpoint_session_config(
            session
        )
        checkpoint = await self.checkpoint_service().capture(
            ctx,
            label=label,
            extra=payload,
        )
        session.tape_id = ctx.tape.tape_id
        await self.persist_session(session)
        return checkpoint


__all__ = [
    "RuntimeCheckpointCaptureBackend",
    "RuntimeCheckpointCaptureBackendProvider",
    "RuntimeCheckpointCaptureContext",
    "RuntimeCheckpointCapturePersister",
    "RuntimeCheckpointCaptureService",
    "RuntimeCheckpointCaptureSession",
    "RuntimeCheckpointRuntimeEnsurer",
]
