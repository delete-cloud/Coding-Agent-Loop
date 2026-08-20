"""Shared runtime-store field validators."""

from __future__ import annotations

from datetime import datetime

from coding_agent.stores.rtstore.json_types import JSONObject


def _require_non_empty(field_name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")


def _normalize_optional_error(error: str | None) -> str | None:
    """Treat empty/whitespace-only error text as absent (None).

    Defense-in-depth: a blank error string is semantically "no error" and must
    never be persisted, otherwise the non-empty invariant on AgentRunRecord
    turns a write/read into a ValueError that masks the real failure.
    """
    if error is None or not error.strip():
        return None
    return error


def _require_datetime(field_name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")


def _require_json_object(field_name: str, value: JSONObject) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive_int(field_name: str, value: int) -> None:
    _require_non_negative_int(field_name, value)
    if value == 0:
        raise ValueError(f"{field_name} must be positive")
