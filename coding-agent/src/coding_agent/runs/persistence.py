from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
from typing import Any, Protocol, cast

from coding_agent.stores.runtime_store import JSONObject, JSONValue, RunMessageSnapshotRecord
from coding_agent.stores import RuntimeCheckpointStore, RuntimeRunLifecycleStore

from .lifecycle import (
    RuntimeObservationCompleter,
    RuntimeRunLifecycle,
    RuntimeRunMetadataProvider,
    RuntimeRunResumeContext,
    RuntimeRunSession,
    RuntimeSessionPersister,
    RuntimeTurnFinalizer,
    RuntimeTurnSession,
)


class RuntimeMessageSnapshotSession(RuntimeTurnSession, Protocol):
    id: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_compatible_value(value: object) -> JSONValue:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return cast(JSONValue, value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_compatible_value(item) for item in value]
    return str(value)


def _runtime_message_snapshot(messages: object) -> list[JSONObject] | None:
    if messages is None:
        return None
    if not isinstance(messages, list):
        raise TypeError("runtime message snapshot source must be a list")
    payload = _json_compatible_value(messages)
    if not isinstance(payload, list):
        raise TypeError("runtime message snapshot must serialize to a JSON array")
    snapshot: list[JSONObject] = []
    for message in payload:
        if not isinstance(message, dict):
            raise TypeError("runtime message snapshot entries must be JSON objects")
        snapshot.append(cast(JSONObject, message))
    return snapshot


@dataclass(frozen=True)
class RuntimeRunPersistenceService:
    run_store: RuntimeRunLifecycleStore | None
    checkpoint_store: RuntimeCheckpointStore | None
    metadata_for_session: RuntimeRunMetadataProvider
    now: Callable[[], datetime] = _utc_now

    @property
    def has_runtime_store(self) -> bool:
        return self.run_store is not None

    def lifecycle(self) -> RuntimeRunLifecycle:
        return RuntimeRunLifecycle(
            store=self.run_store,
            metadata_for_session=self.metadata_for_session,
            now=self.now,
        )

    def turn_finalizer(
        self,
        *,
        persist_session: RuntimeSessionPersister,
        complete_observation: RuntimeObservationCompleter | None = None,
    ) -> RuntimeTurnFinalizer:
        return RuntimeTurnFinalizer(
            has_runtime_store=self.has_runtime_store,
            save_message_snapshot=self.save_message_snapshot,
            finish_run=self.finish_run,
            persist_session=persist_session,
            complete_observation=complete_observation,
        )

    async def finish_run(
        self,
        session: RuntimeRunSession,
        *,
        run_id: str,
        status: str,
        result: JSONObject,
        error: str | None,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> None:
        await self.lifecycle().finish(
            session,
            run_id=run_id,
            status=status,
            result=result,
            error=error,
            resume_context=resume_context,
        )

    async def save_message_snapshot(
        self,
        session: RuntimeMessageSnapshotSession,
        ctx: Any,
        *,
        run_id: str,
    ) -> None:
        if self.checkpoint_store is None:
            return
        messages = _runtime_message_snapshot(getattr(ctx, "messages", None))
        if messages is None:
            return
        await self.checkpoint_store.save_message_snapshot(
            RunMessageSnapshotRecord(
                snapshot_id=f"{run_id}:latest",
                run_id=run_id,
                messages=messages,
                metadata={
                    "session_id": session.id,
                    "tape_id": session.tape_id,
                    "message_count": len(messages),
                    "snapshot_kind": "latest_context",
                },
                created_at=self.now(),
            )
        )


__all__ = [
    "RuntimeMessageSnapshotSession",
    "RuntimeRunPersistenceService",
]
