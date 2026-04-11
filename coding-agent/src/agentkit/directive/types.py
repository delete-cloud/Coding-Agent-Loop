from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Directive:
    kind: str = field(init=False)


@dataclass(frozen=True)
class Approve(Directive):
    kind: str = field(init=False, default="approve")


@dataclass(frozen=True)
class Reject(Directive):
    reason: str = ""
    kind: str = field(init=False, default="reject")


@dataclass(frozen=True)
class AskUser(Directive):
    question: str = ""
    kind: str = field(init=False, default="ask_user")


@dataclass(frozen=True)
class Checkpoint(Directive):
    plugin_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    kind: str = field(init=False, default="checkpoint")


@dataclass(frozen=True)
class MemoryRecord(Directive):
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    kind: str = field(init=False, default="memory_record")
