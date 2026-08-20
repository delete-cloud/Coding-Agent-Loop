"""Durable runtime record types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from coding_agent.stores.rtstore.json_types import JSONObject, JSONScalar, JSONValue
from coding_agent.stores.rtstore.validate import (
    _require_datetime,
    _require_json_object,
    _require_non_empty,
    _require_positive_int,
)

__all__ = [
    "AgentInteractionRecord",
    "AgentRunRecord",
    "JSONObject",
    "JSONScalar",
    "JSONValue",
    "RunMessageSnapshotRecord",
    "RuntimeEventRecord",
]


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    session_id: str
    tape_id: str | None
    parent_run_id: str | None
    agent_id: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    metadata: JSONObject = field(default_factory=dict)
    result: JSONObject = field(default_factory=dict)
    error: str | None = None
    superseded_by_checkpoint_id: str | None = None
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("session_id", self.session_id)
        _require_non_empty("status", self.status)
        if self.tape_id is not None:
            _require_non_empty("tape_id", self.tape_id)
        if self.parent_run_id is not None:
            _require_non_empty("parent_run_id", self.parent_run_id)
        if self.agent_id is not None:
            _require_non_empty("agent_id", self.agent_id)
        _require_datetime("started_at", self.started_at)
        if self.ended_at is not None:
            _require_datetime("ended_at", self.ended_at)
        _require_json_object("metadata", self.metadata)
        _require_json_object("result", self.result)
        if self.error is not None:
            _require_non_empty("error", self.error)
        if self.superseded_by_checkpoint_id is not None:
            _require_non_empty(
                "superseded_by_checkpoint_id",
                self.superseded_by_checkpoint_id,
            )
        if self.superseded_at is not None:
            _require_datetime("superseded_at", self.superseded_at)


@dataclass(frozen=True)
class RuntimeEventRecord:
    event_id: str
    run_id: str
    event_kind: str
    payload: JSONObject
    created_at: datetime
    sequence: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty("event_id", self.event_id)
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("event_kind", self.event_kind)
        _require_json_object("payload", self.payload)
        _require_datetime("created_at", self.created_at)
        if self.sequence is not None:
            _require_positive_int("sequence", self.sequence)


@dataclass(frozen=True)
class RunMessageSnapshotRecord:
    snapshot_id: str
    run_id: str
    messages: list[JSONObject]
    metadata: JSONObject
    created_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty("snapshot_id", self.snapshot_id)
        _require_non_empty("run_id", self.run_id)
        if not isinstance(self.messages, list):
            raise TypeError("messages must be a list")
        for message in self.messages:
            _require_json_object("message", message)
        _require_json_object("metadata", self.metadata)
        _require_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class AgentInteractionRecord:
    interaction_id: str
    run_id: str
    interaction_kind: str
    status: str
    request_payload: JSONObject
    response_payload: JSONObject
    metadata: JSONObject
    created_at: datetime
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty("interaction_id", self.interaction_id)
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("interaction_kind", self.interaction_kind)
        _require_non_empty("status", self.status)
        _require_json_object("request_payload", self.request_payload)
        _require_json_object("response_payload", self.response_payload)
        _require_json_object("metadata", self.metadata)
        _require_datetime("created_at", self.created_at)
        if self.resolved_at is not None:
            _require_datetime("resolved_at", self.resolved_at)
