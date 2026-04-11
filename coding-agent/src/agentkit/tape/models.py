from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, cast, override

from agentkit._types import EntryKind, JsonDict

AnchorType = Literal["handoff", "topic_start", "topic_end", "fold"]


def _require_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a str")
    return value


def _require_float(data: Mapping[str, object], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number")
    return float(value)


def _require_json_dict(data: Mapping[str, object], key: str) -> JsonDict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be a dict")
    return cast(JsonDict, value)


def _optional_json_dict(data: Mapping[str, object], key: str) -> JsonDict:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be a dict")
    return cast(JsonDict, value)


def _optional_source_ids(data: Mapping[str, object]) -> tuple[str, ...]:
    value = data.get("source_ids")
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TypeError("source_ids must be a sequence of strings")
    source_ids = tuple(value)
    if not all(isinstance(item, str) for item in source_ids):
        raise TypeError("source_ids must contain only strings")
    return cast(tuple[str, ...], source_ids)


@dataclass(frozen=True)
class Entry:
    kind: EntryKind
    payload: JsonDict
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    meta: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        data: JsonDict = {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }
        if self.meta:
            data["meta"] = self.meta
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Entry:
        kind = _require_str(data, "kind")
        if kind == "anchor":
            anchor_type = data.get("anchor_type")
            if anchor_type is None:
                anchor_type = _optional_json_dict(data, "meta").get("anchor_type")
            if anchor_type is not None:
                if not isinstance(anchor_type, str):
                    raise TypeError("anchor_type must be a str")
                legacy_anchor_map = {
                    "topic_initial": "topic_start",
                    "topic_finalized": "topic_end",
                }
                promoted = dict(data)
                promoted["anchor_type"] = legacy_anchor_map.get(
                    anchor_type, anchor_type
                )
                return Anchor.from_dict(promoted)
        return cls(
            id=_require_str(data, "id"),
            kind=cast(EntryKind, kind),
            payload=_require_json_dict(data, "payload"),
            timestamp=_require_float(data, "timestamp"),
            meta=_optional_json_dict(data, "meta"),
        )


@dataclass(frozen=True)
class Anchor(Entry):
    kind: EntryKind = field(default="anchor", init=False)
    anchor_type: AnchorType = "handoff"
    source_ids: tuple[str, ...] = ()

    @property
    def is_handoff(self) -> bool:
        return self.anchor_type == "handoff"

    @property
    def fold_boundary(self) -> bool:
        return self.anchor_type in ("fold", "topic_end")

    @override
    def to_dict(self) -> JsonDict:
        data = super().to_dict()
        data["anchor_type"] = self.anchor_type
        if self.source_ids:
            data["source_ids"] = list(self.source_ids)
        return data

    @classmethod
    @override
    def from_dict(cls, data: Mapping[str, object]) -> Anchor:
        anchor_type = data.get("anchor_type", "handoff")
        if not isinstance(anchor_type, str):
            raise TypeError("anchor_type must be a str")
        return cls(
            id=_require_str(data, "id"),
            payload=_require_json_dict(data, "payload"),
            timestamp=_require_float(data, "timestamp"),
            meta=_optional_json_dict(data, "meta"),
            anchor_type=cast(AnchorType, anchor_type),
            source_ids=_optional_source_ids(data),
        )


__all__ = ["Anchor", "AnchorType", "Entry", "EntryKind"]
