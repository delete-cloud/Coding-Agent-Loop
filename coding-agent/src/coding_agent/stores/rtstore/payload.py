"""JSONL payload codecs for runtime records."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from coding_agent.stores.rtstore.records import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.stores.rtstore.validate import _require_datetime


def _agent_run_to_payload(record: AgentRunRecord) -> JSONObject:
    return {
        "run_id": record.run_id,
        "session_id": record.session_id,
        "tape_id": record.tape_id,
        "parent_run_id": record.parent_run_id,
        "agent_id": record.agent_id,
        "status": record.status,
        "started_at": _datetime_to_json(record.started_at),
        "ended_at": (
            None if record.ended_at is None else _datetime_to_json(record.ended_at)
        ),
        "metadata": record.metadata,
        "result": record.result,
        "error": record.error,
        "superseded_by_checkpoint_id": record.superseded_by_checkpoint_id,
        "superseded_at": (
            None
            if record.superseded_at is None
            else _datetime_to_json(record.superseded_at)
        ),
    }


def _agent_run_from_payload(payload: JSONObject) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=_required_payload_str(payload, "run_id", context="agent run payload"),
        session_id=_required_payload_str(
            payload,
            "session_id",
            context="agent run payload",
        ),
        tape_id=_optional_payload_str(payload, "tape_id", context="agent run payload"),
        parent_run_id=_optional_payload_str(
            payload,
            "parent_run_id",
            context="agent run payload",
        ),
        agent_id=_optional_payload_str(
            payload, "agent_id", context="agent run payload"
        ),
        status=_required_payload_str(payload, "status", context="agent run payload"),
        started_at=_required_payload_datetime(
            payload,
            "started_at",
            context="agent run payload",
        ),
        ended_at=_optional_payload_datetime(
            payload,
            "ended_at",
            context="agent run payload",
        ),
        metadata=_required_payload_json_object(
            payload,
            "metadata",
            context="agent run payload",
        ),
        result=_required_payload_json_object(
            payload,
            "result",
            context="agent run payload",
        ),
        error=_optional_payload_str(payload, "error", context="agent run payload"),
        superseded_by_checkpoint_id=_optional_payload_str(
            payload,
            "superseded_by_checkpoint_id",
            context="agent run payload",
        ),
        superseded_at=_optional_payload_datetime(
            payload,
            "superseded_at",
            context="agent run payload",
        ),
    )


def _runtime_event_to_payload(record: RuntimeEventRecord) -> JSONObject:
    return {
        "sequence": record.sequence,
        "event_id": record.event_id,
        "run_id": record.run_id,
        "event_kind": record.event_kind,
        "payload": record.payload,
        "created_at": _datetime_to_json(record.created_at),
    }


def _runtime_event_from_payload(payload: JSONObject) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        sequence=_required_payload_int(
            payload,
            "sequence",
            context="runtime event payload",
        ),
        event_id=_required_payload_str(
            payload,
            "event_id",
            context="runtime event payload",
        ),
        run_id=_required_payload_str(
            payload, "run_id", context="runtime event payload"
        ),
        event_kind=_required_payload_str(
            payload,
            "event_kind",
            context="runtime event payload",
        ),
        payload=_required_payload_json_object(
            payload,
            "payload",
            context="runtime event payload",
        ),
        created_at=_required_payload_datetime(
            payload,
            "created_at",
            context="runtime event payload",
        ),
    )


def _message_snapshot_to_payload(record: RunMessageSnapshotRecord) -> JSONObject:
    return {
        "snapshot_id": record.snapshot_id,
        "run_id": record.run_id,
        "messages": record.messages,
        "metadata": record.metadata,
        "created_at": _datetime_to_json(record.created_at),
    }


def _message_snapshot_from_payload(payload: JSONObject) -> RunMessageSnapshotRecord:
    return RunMessageSnapshotRecord(
        snapshot_id=_required_payload_str(
            payload,
            "snapshot_id",
            context="message snapshot payload",
        ),
        run_id=_required_payload_str(
            payload,
            "run_id",
            context="message snapshot payload",
        ),
        messages=_required_payload_message_list(payload, "messages"),
        metadata=_required_payload_json_object(
            payload,
            "metadata",
            context="message snapshot payload",
        ),
        created_at=_required_payload_datetime(
            payload,
            "created_at",
            context="message snapshot payload",
        ),
    )


def _interaction_to_payload(record: AgentInteractionRecord) -> JSONObject:
    return {
        "interaction_id": record.interaction_id,
        "run_id": record.run_id,
        "interaction_kind": record.interaction_kind,
        "status": record.status,
        "request_payload": record.request_payload,
        "response_payload": record.response_payload,
        "metadata": record.metadata,
        "created_at": _datetime_to_json(record.created_at),
        "resolved_at": (
            None
            if record.resolved_at is None
            else _datetime_to_json(record.resolved_at)
        ),
    }


def _interaction_from_payload(payload: JSONObject) -> AgentInteractionRecord:
    return AgentInteractionRecord(
        interaction_id=_required_payload_str(
            payload,
            "interaction_id",
            context="agent interaction payload",
        ),
        run_id=_required_payload_str(
            payload,
            "run_id",
            context="agent interaction payload",
        ),
        interaction_kind=_required_payload_str(
            payload,
            "interaction_kind",
            context="agent interaction payload",
        ),
        status=_required_payload_str(
            payload,
            "status",
            context="agent interaction payload",
        ),
        request_payload=_required_payload_json_object(
            payload,
            "request_payload",
            context="agent interaction payload",
        ),
        response_payload=_required_payload_json_object(
            payload,
            "response_payload",
            context="agent interaction payload",
        ),
        metadata=_required_payload_json_object(
            payload,
            "metadata",
            context="agent interaction payload",
        ),
        created_at=_required_payload_datetime(
            payload,
            "created_at",
            context="agent interaction payload",
        ),
        resolved_at=_optional_payload_datetime(
            payload,
            "resolved_at",
            context="agent interaction payload",
        ),
    )


def _required_str(row: dict[str, object], key: str, *, context: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string {key}")
    return value


def _optional_str(row: dict[str, object], key: str, *, context: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"postgres {context} must include string or None {key}")
    return value


def _required_int(row: dict[str, object], key: str, *, context: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"postgres {context} must include int {key}")
    return value


def _required_datetime(row: dict[str, object], key: str, *, context: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime {key}")
    return value


def _optional_datetime(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"postgres {context} must include datetime or None {key}")
    return value


def _required_json_object(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> JSONObject:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres {context} must include JSON object {key}")
    return cast(JSONObject, value)


def _required_message_list(row: dict[str, object], key: str) -> list[JSONObject]:
    value = row.get(key)
    if not isinstance(value, list):
        raise TypeError("postgres message snapshot row must include list messages")
    messages: list[JSONObject] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("postgres message snapshot messages must contain objects")
        messages.append(cast(JSONObject, item))
    return messages


def _datetime_to_json(value: datetime) -> str:
    _require_datetime("datetime", value)
    return value.isoformat()


def _required_payload_str(payload: JSONObject, key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{context} must include string {key}")
    return value


def _optional_payload_str(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{context} must include string or None {key}")
    return value


def _required_payload_int(payload: JSONObject, key: str, *, context: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{context} must include int {key}")
    return value


def _required_payload_datetime(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> datetime:
    value = _required_payload_str(payload, key, context=context)
    return datetime.fromisoformat(value)


def _optional_payload_datetime(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{context} must include datetime string or None {key}")
    return datetime.fromisoformat(value)


def _required_payload_json_object(
    payload: JSONObject,
    key: str,
    *,
    context: str,
) -> JSONObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{context} must include JSON object {key}")
    return cast(JSONObject, value)


def _required_payload_message_list(payload: JSONObject, key: str) -> list[JSONObject]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise TypeError("message snapshot payload must include list messages")
    messages: list[JSONObject] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("message snapshot payload messages must contain objects")
        messages.append(cast(JSONObject, item))
    return messages
