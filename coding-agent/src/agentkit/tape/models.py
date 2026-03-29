from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentkit._types import EntryKind


@dataclass(frozen=True)
class Entry:
    kind: EntryKind
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=data["payload"],
            timestamp=data["timestamp"],
        )
