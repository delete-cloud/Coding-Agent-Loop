from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Protocol, cast

from coding_agent.runtime_store import (
    AgentRunRecord,
    JSONObject,
    JSONValue,
    RuntimeEventRecord,
)
from coding_agent.wire.protocol import WireMessage

from .target import (
    RunTarget,
    run_target_execution_placement,
    run_target_execution_plane,
    run_target_executor_kind,
    run_target_executor_ref_kind,
    run_target_workspace_surface,
)


class RuntimeWireEventSession(Protocol):
    id: str
    current_turn_id: str | None
    tape_id: str | None
    default_run_target: RunTarget


class RuntimeWireEventStore(Protocol):
    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None: ...

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord: ...


def runtime_event_correlation_from_run(run: AgentRunRecord) -> JSONObject:
    metadata = run.metadata
    payload: JSONObject = {
        "session_id": run.session_id,
        "run_id": run.run_id,
    }
    if run.tape_id is not None:
        payload["tape_id"] = run.tape_id
    for key in (
        "execution_placement",
        "executor_kind",
        "executor_ref_kind",
        "workspace_surface",
        "execution_plane",
        "previous_run_id",
        "resume_from_run_id",
        "resume_from_event_id",
        "resume_reason",
        "resume_context_strategy",
        "resume_boundary_anchor_id",
        "resume_boundary_anchor_type",
        "executor_id",
        "worker_id",
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            payload[key] = value
    if metadata.get("resume_context_injected") is True:
        payload["resume_context_injected"] = True
    return payload


def with_runtime_event_correlation(
    payload: JSONObject,
    correlation: JSONObject,
) -> JSONObject:
    return {**correlation, **payload}


@dataclass(frozen=True)
class RuntimeWireEventRecorder:
    store: RuntimeWireEventStore | None
    new_event_id: Callable[[str], str] = lambda run_id: (
        f"{run_id}:wire:{uuid.uuid4().hex}"
    )

    async def append_wire_event(
        self,
        session: RuntimeWireEventSession,
        message: WireMessage,
    ) -> RuntimeEventRecord | None:
        if self.store is None:
            return None
        run_id = session.current_turn_id
        if run_id is None:
            return None
        run = await self.store.load_agent_run(run_id)
        if run is None:
            correlation = _runtime_event_correlation_from_session(session, run_id)
        else:
            correlation = runtime_event_correlation_from_run(run)
        return await self.store.append_runtime_event(
            RuntimeEventRecord(
                event_id=self.new_event_id(run_id),
                run_id=run_id,
                event_kind=f"wire.{type(message).__name__}",
                payload=with_runtime_event_correlation(
                    _wire_message_event_payload(message),
                    correlation,
                ),
                created_at=message.timestamp,
            )
        )


def _runtime_event_correlation_from_session(
    session: RuntimeWireEventSession,
    run_id: str,
) -> JSONObject:
    target = session.default_run_target
    correlation: JSONObject = {
        "session_id": session.id,
        "run_id": run_id,
        "execution_placement": run_target_execution_placement(target),
        "executor_kind": run_target_executor_kind(target),
        "workspace_surface": run_target_workspace_surface(target),
        "execution_plane": run_target_execution_plane(target),
    }
    if session.tape_id is not None:
        correlation["tape_id"] = session.tape_id
    executor_ref_kind = run_target_executor_ref_kind(target)
    if executor_ref_kind is not None:
        correlation["executor_ref_kind"] = executor_ref_kind
    return correlation


def _wire_message_event_payload(message: WireMessage) -> JSONObject:
    message_payload = _json_compatible_value(asdict(message))
    if not isinstance(message_payload, dict):
        raise TypeError("wire message payload must serialize to a JSON object")
    return {
        "message_type": type(message).__name__,
        "message": cast(JSONValue, message_payload),
    }


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


__all__ = [
    "RuntimeWireEventRecorder",
    "RuntimeWireEventSession",
    "RuntimeWireEventStore",
    "runtime_event_correlation_from_run",
    "with_runtime_event_correlation",
]
