"""SQLite row codecs for runtime records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import cast

from agentkit.runtime.contracts import CommitRef, OperationStateVersion
from coding_agent.stores.rtstore.records import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    JSONValue,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.stores.rtstore.payload import _datetime_to_json


def _json_to_sql(value: JSONValue) -> str:
    return json.dumps(value, sort_keys=True)


def _json_from_sql(value: object, *, context: str) -> JSONValue:
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} must include JSON text")
    loaded = json.loads(value)
    return cast(JSONValue, loaded)


def _json_object_from_sql(value: object, *, context: str) -> JSONObject:
    loaded = _json_from_sql(value, context=context)
    if not isinstance(loaded, dict):
        raise TypeError(f"sqlite {context} must decode to a JSON object")
    return cast(JSONObject, loaded)


def _message_list_from_sql(value: object, *, context: str) -> list[JSONObject]:
    loaded = _json_from_sql(value, context=context)
    if not isinstance(loaded, list):
        raise TypeError(f"sqlite {context} must decode to a list")
    messages: list[JSONObject] = []
    for item in loaded:
        if not isinstance(item, dict):
            raise TypeError(f"sqlite {context} messages must contain objects")
        messages.append(cast(JSONObject, item))
    return messages


def _sqlite_value(row: sqlite3.Row, key: str, *, context: str) -> object:
    try:
        return row[key]
    except (IndexError, KeyError) as exc:
        raise TypeError(f"sqlite {context} row must include {key}") from exc


def _sqlite_required_str(row: sqlite3.Row, key: str, *, context: str) -> str:
    value = _sqlite_value(row, key, context=context)
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} row must include string {key}")
    return value


def _sqlite_optional_str(row: sqlite3.Row, key: str, *, context: str) -> str | None:
    value = _sqlite_value(row, key, context=context)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"sqlite {context} row must include string or NULL {key}")
    return value


def _sqlite_optional_int(row: sqlite3.Row, key: str, *, context: str) -> int | None:
    value = _sqlite_value(row, key, context=context)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"sqlite {context} row must include int or NULL {key}")
    return value


def _sqlite_required_int(row: sqlite3.Row, key: str, *, context: str) -> int:
    value = _sqlite_value(row, key, context=context)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"sqlite {context} row must include int {key}")
    return value


def _sqlite_required_datetime(row: sqlite3.Row, key: str, *, context: str) -> datetime:
    return datetime.fromisoformat(_sqlite_required_str(row, key, context=context))


def _sqlite_optional_datetime(
    row: sqlite3.Row,
    key: str,
    *,
    context: str,
) -> datetime | None:
    value = _sqlite_optional_str(row, key, context=context)
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _operation_state_from_sqlite_row(row: sqlite3.Row) -> OperationStateVersion:
    fact_seq_start = _sqlite_optional_int(
        row,
        "fact_seq_start",
        context="operation state",
    )
    fact_seq_end = _sqlite_optional_int(
        row,
        "fact_seq_end",
        context="operation state",
    )
    return OperationStateVersion(
        run_id=_sqlite_required_str(row, "run_id", context="operation state"),
        revision=_sqlite_required_int(row, "revision", context="operation state"),
        projection_epoch=_sqlite_required_int(
            row,
            "projection_epoch",
            context="operation state",
        ),
        commit_ref=CommitRef(
            transition_id=_sqlite_required_str(
                row,
                "transition_id",
                context="operation state",
            ),
            fact_seq_start=(None if fact_seq_start is None else str(fact_seq_start)),
            fact_seq_end=None if fact_seq_end is None else str(fact_seq_end),
        ),
        value=_json_object_from_sql(row["value"], context="operation state value"),
    )


def _transition_receipt_from_sqlite_row(
    row: sqlite3.Row,
) -> tuple[str, JSONObject]:
    return (
        _sqlite_required_str(
            row,
            "mutation_fingerprint",
            context="transition receipt",
        ),
        _json_object_from_sql(
            row["result"],
            context="transition receipt result",
        ),
    )


def _agent_run_sqlite_values(record: AgentRunRecord) -> tuple[object, ...]:
    return (
        record.run_id,
        record.session_id,
        record.tape_id,
        record.parent_run_id,
        record.agent_id,
        record.status,
        _datetime_to_json(record.started_at),
        None if record.ended_at is None else _datetime_to_json(record.ended_at),
        _json_to_sql(record.metadata),
        _json_to_sql(record.result),
        record.error,
        record.superseded_by_checkpoint_id,
        (
            None
            if record.superseded_at is None
            else _datetime_to_json(record.superseded_at)
        ),
    )


def _agent_run_from_sqlite_row(row: sqlite3.Row) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=_sqlite_required_str(row, "run_id", context="agent run"),
        session_id=_sqlite_required_str(row, "session_id", context="agent run"),
        tape_id=_sqlite_optional_str(row, "tape_id", context="agent run"),
        parent_run_id=_sqlite_optional_str(row, "parent_run_id", context="agent run"),
        agent_id=_sqlite_optional_str(row, "agent_id", context="agent run"),
        status=_sqlite_required_str(row, "status", context="agent run"),
        started_at=_sqlite_required_datetime(row, "started_at", context="agent run"),
        ended_at=_sqlite_optional_datetime(row, "ended_at", context="agent run"),
        metadata=_json_object_from_sql(row["metadata"], context="agent run metadata"),
        result=_json_object_from_sql(row["result"], context="agent run result"),
        error=_sqlite_optional_str(row, "error", context="agent run"),
        superseded_by_checkpoint_id=_sqlite_optional_str(
            row,
            "superseded_by_checkpoint_id",
            context="agent run",
        ),
        superseded_at=_sqlite_optional_datetime(
            row,
            "superseded_at",
            context="agent run",
        ),
    )


def _runtime_event_from_sqlite_row(row: sqlite3.Row) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        sequence=_sqlite_required_int(row, "sequence", context="runtime event"),
        event_id=_sqlite_required_str(row, "event_id", context="runtime event"),
        run_id=_sqlite_required_str(row, "run_id", context="runtime event"),
        event_kind=_sqlite_required_str(row, "event_kind", context="runtime event"),
        payload=_json_object_from_sql(
            row["payload"],
            context="runtime event payload",
        ),
        created_at=_sqlite_required_datetime(
            row,
            "created_at",
            context="runtime event",
        ),
    )


def _message_snapshot_from_sqlite_row(
    row: sqlite3.Row,
) -> RunMessageSnapshotRecord:
    return RunMessageSnapshotRecord(
        snapshot_id=_sqlite_required_str(
            row,
            "snapshot_id",
            context="message snapshot",
        ),
        run_id=_sqlite_required_str(row, "run_id", context="message snapshot"),
        messages=_message_list_from_sql(
            row["messages"],
            context="message snapshot messages",
        ),
        metadata=_json_object_from_sql(
            row["metadata"],
            context="message snapshot metadata",
        ),
        created_at=_sqlite_required_datetime(
            row,
            "created_at",
            context="message snapshot",
        ),
    )


def _interaction_sqlite_values(record: AgentInteractionRecord) -> tuple[object, ...]:
    return (
        record.interaction_id,
        record.run_id,
        record.interaction_kind,
        record.status,
        _json_to_sql(record.request_payload),
        _json_to_sql(record.response_payload),
        _json_to_sql(record.metadata),
        _datetime_to_json(record.created_at),
        None if record.resolved_at is None else _datetime_to_json(record.resolved_at),
    )


def _interaction_from_sqlite_row(row: sqlite3.Row) -> AgentInteractionRecord:
    return AgentInteractionRecord(
        interaction_id=_sqlite_required_str(
            row,
            "interaction_id",
            context="agent interaction",
        ),
        run_id=_sqlite_required_str(row, "run_id", context="agent interaction"),
        interaction_kind=_sqlite_required_str(
            row,
            "interaction_kind",
            context="agent interaction",
        ),
        status=_sqlite_required_str(row, "status", context="agent interaction"),
        request_payload=_json_object_from_sql(
            row["request_payload"],
            context="agent interaction request payload",
        ),
        response_payload=_json_object_from_sql(
            row["response_payload"],
            context="agent interaction response payload",
        ),
        metadata=_json_object_from_sql(
            row["metadata"],
            context="agent interaction metadata",
        ),
        created_at=_sqlite_required_datetime(
            row,
            "created_at",
            context="agent interaction",
        ),
        resolved_at=_sqlite_optional_datetime(
            row,
            "resolved_at",
            context="agent interaction",
        ),
    )
