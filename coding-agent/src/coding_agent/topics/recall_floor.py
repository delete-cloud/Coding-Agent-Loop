"""Shared recall relevance floor validation."""

from __future__ import annotations


def validate_recall_floor(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    normalized = float(value)
    if not 0 <= normalized <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return normalized
