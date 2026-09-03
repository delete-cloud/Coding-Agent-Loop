from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.checkpoint.models import CheckpointMeta
from agentkit.runtime.contracts import OperationStateVersion

from coding_agent.runtime_activation import (
    CHECKPOINT_FORMAT_KEY,
    OPERATION_STATE_VERSION_KEY,
    RUNTIME_VERSION_NEW,
)


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
RestorePointStateLoader = Callable[
    [str, str],
    Awaitable[OperationStateVersion | None],
]


def serialize_operation_state_version(
    state: OperationStateVersion | None,
) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "run_id": state.run_id,
        "revision": state.revision,
        "projection_epoch": state.projection_epoch,
        "commit_ref": {
            "transition_id": state.commit_ref.transition_id,
            "fact_seq_start": state.commit_ref.fact_seq_start,
            "fact_seq_end": state.commit_ref.fact_seq_end,
        },
        "value": dict(state.value),
    }


@dataclass(frozen=True)
class RuntimeCheckpointCaptureService:
    checkpoint_service: RuntimeCheckpointCaptureBackendProvider
    ensure_runtime: RuntimeCheckpointRuntimeEnsurer
    persist_session: RuntimeCheckpointCapturePersister
    load_operation_state: RestorePointStateLoader | None = None

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
        if OPERATION_STATE_VERSION_KEY in payload:
            raise ValueError(
                f"'{OPERATION_STATE_VERSION_KEY}' is a reserved checkpoint metadata key and cannot be provided via extra"
            )
        payload[CHECKPOINT_SESSION_CONFIG_KEY] = serialize_checkpoint_session_config(
            session
        )
        backend = self.checkpoint_service()
        if getattr(session, "runtime_version", None) == RUNTIME_VERSION_NEW:
            payload[CHECKPOINT_FORMAT_KEY] = RUNTIME_VERSION_NEW
            run_id = getattr(session, "current_turn_id", None)
            state = None
            if (
                isinstance(run_id, str)
                and run_id
                and self.load_operation_state is not None
            ):
                state = await self.load_operation_state(session.id, run_id)
            payload[OPERATION_STATE_VERSION_KEY] = serialize_operation_state_version(
                state
            )
            capture_restore_point = getattr(backend, "capture_restore_point", None)
            if capture_restore_point is None:
                raise TypeError(
                    "checkpoint backend missing capture_restore_point for new-runtime"
                )
            checkpoint = await capture_restore_point(
                tape_id=ctx.tape.tape_id,
                session_id=session.id,
                label=label,
                extra=payload,
            )
        else:
            checkpoint = await backend.capture(
                ctx,
                label=label,
                extra=payload,
            )
        session.tape_id = ctx.tape.tape_id
        await self.persist_session(session)
        return checkpoint


__all__ = [
    "RestorePointStateLoader",
    "RuntimeCheckpointCaptureBackend",
    "RuntimeCheckpointCaptureBackendProvider",
    "RuntimeCheckpointCaptureContext",
    "RuntimeCheckpointCapturePersister",
    "RuntimeCheckpointCaptureService",
    "RuntimeCheckpointCaptureSession",
    "RuntimeCheckpointRuntimeEnsurer",
    "serialize_operation_state_version",
]
