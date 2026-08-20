"""Local durable helper validators."""

from __future__ import annotations

import sqlite3


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_json_object(field_name: str, value: object) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a JSON object")


def _row_required_int(row: sqlite3.Row | None, key: str, *, context: str) -> int:
    if row is None:
        raise TypeError(f"{context} row is missing")
    value = row[key]
    if not isinstance(value, int):
        raise TypeError(f"{context} {key} must be an int")
    return value
