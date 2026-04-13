from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CheckpointMeta:
    checkpoint_id: str
    tape_id: str
    session_id: str | None
    entry_count: int
    window_start: int
    created_at: datetime
    label: str | None = None


@dataclass(frozen=True)
class CheckpointSnapshot:
    meta: CheckpointMeta
    tape_entries: tuple[dict[str, Any], ...]
    plugin_states: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)
