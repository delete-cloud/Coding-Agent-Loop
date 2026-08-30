"""PostgreSQL row codecs for runtime records."""

from __future__ import annotations

from agentkit.runtime.contracts import CommitRef, OperationStateVersion

from coding_agent.stores.rtstore.records import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.stores.rtstore.payload import (
    _optional_datetime,
    _optional_str,
    _required_datetime,
    _required_int,
    _required_json_object,
    _required_message_list,
    _required_str,
)


def _required_row(
    row: dict[str, object] | None,
    context: str,
) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _optional_non_negative_int(
    row: dict[str, object],
    key: str,
    *,
    context: str,
) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{context} must include non-negative integer or NULL {key}")
    return value


def _operation_state_from_row(row: dict[str, object]) -> OperationStateVersion:
    fact_seq_start = _optional_non_negative_int(
        row,
        "fact_seq_start",
        context="operation state row",
    )
    fact_seq_end = _optional_non_negative_int(
        row,
        "fact_seq_end",
        context="operation state row",
    )
    return OperationStateVersion(
        run_id=_required_str(row, "run_id", context="operation state row"),
        revision=_required_int(row, "revision", context="operation state row"),
        projection_epoch=_required_int(
            row,
            "projection_epoch",
            context="operation state row",
        ),
        commit_ref=CommitRef(
            transition_id=_required_str(
                row,
                "transition_id",
                context="operation state row",
            ),
            fact_seq_start=(None if fact_seq_start is None else str(fact_seq_start)),
            fact_seq_end=None if fact_seq_end is None else str(fact_seq_end),
        ),
        value=_required_json_object(
            row,
            "value",
            context="operation state row",
        ),
    )


def _transition_receipt_from_row(
    row: dict[str, object],
) -> tuple[str, JSONObject]:
    return (
        _required_str(
            row,
            "mutation_fingerprint",
            context="transition receipt row",
        ),
        _required_json_object(
            row,
            "result",
            context="transition receipt row",
        ),
    )


def _agent_run_from_row(row: dict[str, object]) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=_required_str(row, "run_id", context="agent run row"),
        session_id=_required_str(row, "session_id", context="agent run row"),
        tape_id=_optional_str(row, "tape_id", context="agent run row"),
        parent_run_id=_optional_str(row, "parent_run_id", context="agent run row"),
        agent_id=_optional_str(row, "agent_id", context="agent run row"),
        status=_required_str(row, "status", context="agent run row"),
        started_at=_required_datetime(row, "started_at", context="agent run row"),
        ended_at=_optional_datetime(row, "ended_at", context="agent run row"),
        metadata=_required_json_object(row, "metadata", context="agent run row"),
        result=_required_json_object(row, "result", context="agent run row"),
        error=_optional_str(row, "error", context="agent run row"),
        superseded_by_checkpoint_id=_optional_str(
            row,
            "superseded_by_checkpoint_id",
            context="agent run row",
        ),
        superseded_at=_optional_datetime(
            row,
            "superseded_at",
            context="agent run row",
        ),
    )


def _runtime_event_from_row(row: dict[str, object]) -> RuntimeEventRecord:
    return RuntimeEventRecord(
        sequence=_required_int(row, "sequence", context="runtime event row"),
        event_id=_required_str(row, "event_id", context="runtime event row"),
        run_id=_required_str(row, "run_id", context="runtime event row"),
        event_kind=_required_str(row, "event_kind", context="runtime event row"),
        payload=_required_json_object(row, "payload", context="runtime event row"),
        created_at=_required_datetime(row, "created_at", context="runtime event row"),
    )


def _message_snapshot_from_row(
    row: dict[str, object],
) -> RunMessageSnapshotRecord:
    return RunMessageSnapshotRecord(
        snapshot_id=_required_str(
            row,
            "snapshot_id",
            context="message snapshot row",
        ),
        run_id=_required_str(row, "run_id", context="message snapshot row"),
        messages=_required_message_list(row, "messages"),
        metadata=_required_json_object(
            row,
            "metadata",
            context="message snapshot row",
        ),
        created_at=_required_datetime(
            row,
            "created_at",
            context="message snapshot row",
        ),
    )


def _interaction_from_row(row: dict[str, object]) -> AgentInteractionRecord:
    return AgentInteractionRecord(
        interaction_id=_required_str(
            row,
            "interaction_id",
            context="agent interaction row",
        ),
        run_id=_required_str(row, "run_id", context="agent interaction row"),
        interaction_kind=_required_str(
            row,
            "interaction_kind",
            context="agent interaction row",
        ),
        status=_required_str(row, "status", context="agent interaction row"),
        request_payload=_required_json_object(
            row,
            "request_payload",
            context="agent interaction row",
        ),
        response_payload=_required_json_object(
            row,
            "response_payload",
            context="agent interaction row",
        ),
        metadata=_required_json_object(
            row,
            "metadata",
            context="agent interaction row",
        ),
        created_at=_required_datetime(
            row,
            "created_at",
            context="agent interaction row",
        ),
        resolved_at=_optional_datetime(
            row,
            "resolved_at",
            context="agent interaction row",
        ),
    )
