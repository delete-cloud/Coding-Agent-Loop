"""PostgreSQL durable helper validators."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from agentkit.checkpoint.models import CheckpointMeta
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)


def _require_payload_session(
    authority: OwnerAuthority,
    payload: dict[str, Any],
) -> None:
    payload_id = payload.get("id")
    if payload_id != authority.session_id:
        raise SessionOwnershipConflictError("session payload belongs to another owner")
    payload_session_id = payload.get("session_id")
    if payload_session_id is not None and payload_session_id != authority.session_id:
        raise SessionOwnershipConflictError("session payload belongs to another owner")


def _checkpoint_meta_payload(meta: CheckpointMeta) -> dict[str, Any]:
    return {
        "checkpoint_id": meta.checkpoint_id,
        "tape_id": meta.tape_id,
        "session_id": meta.session_id,
        "entry_count": meta.entry_count,
        "window_start": meta.window_start,
        "created_at": meta.created_at.isoformat(),
        "label": meta.label,
    }


def _required_row(row: dict[str, object] | None, context: str) -> dict[str, object]:
    if row is None:
        raise RuntimeError(f"postgres {context} returned no row")
    return row


def _required_owned_row(
    row: dict[str, object] | None,
    conflict_message: str,
) -> dict[str, object]:
    if row is None:
        raise SessionOwnershipConflictError(conflict_message)
    return row


def _required_str(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise TypeError(f"postgres row must include string {key}")
    return value


def _required_dict(row: dict[str, object], key: str) -> dict[str, object]:
    value = row.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"postgres row must include dict {key}")
    return cast(dict[str, object], value)


def _required_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"postgres row must include int {key}")
    return value


def _optional_str(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"postgres row must include string or None {key}")
    return value


def _optional_int(row: dict[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"postgres row must include int or None {key}")
    return value


def _optional_dict(row: dict[str, object], key: str) -> dict[str, object] | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"postgres row must include dict or None {key}")
    return cast(dict[str, object], value)


def _required_datetime(row: dict[str, object], key: str) -> datetime:
    value = row.get(key)
    if not isinstance(value, datetime):
        raise TypeError(f"postgres row must include datetime {key}")
    return value
