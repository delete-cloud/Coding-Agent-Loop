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
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        kind = data.get("kind")
        if kind == "anchor":
            if "anchor_type" in data:
                from agentkit.tape.anchor import Anchor

                return Anchor.from_dict(data)
            meta_anchor_type = data.get("meta", {}).get("anchor_type")
            if meta_anchor_type:
                from agentkit.tape.anchor import Anchor

                _LEGACY_ANCHOR_MAP = {
                    "topic_initial": "topic_start",
                    "topic_finalized": "topic_end",
                }
                mapped = _LEGACY_ANCHOR_MAP.get(meta_anchor_type, meta_anchor_type)
                promoted = {**data, "anchor_type": mapped}
                return Anchor.from_dict(promoted)
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=data["payload"],
            timestamp=data["timestamp"],
            meta=data.get("meta", {}),
        )
