"""Anchor — structured checkpoint entry for tape windowing and provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agentkit._types import EntryKind
from agentkit.tape.models import Entry

AnchorType = Literal["handoff", "topic_start", "topic_end", "fold"]


@dataclass(frozen=True)
class Anchor(Entry):
    """Typed anchor entry with structured fields replacing implicit meta conventions.

    anchor_type: semantic role of this anchor
    source_ids: Range bounds (first_id, last_id) of entries folded into this anchor (provenance)
    """

    # Override `kind` with a fixed default so callers don't need to pass it.
    # init=False means it's excluded from __init__ and set automatically.
    kind: EntryKind = field(default="anchor", init=False)
    anchor_type: AnchorType = "handoff"
    source_ids: tuple[str, ...] = ()

    @property
    def is_handoff(self) -> bool:
        return self.anchor_type == "handoff"

    @property
    def fold_boundary(self) -> bool:
        """topic_end and fold anchors act as context boundaries.

        ContextBuilder skips these entries so the LLM never sees them.
        They exist only for tape-level bookkeeping (topic segmentation,
        context windowing) and are not user-facing content.
        """
        return self.anchor_type in ("fold", "topic_end")

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["anchor_type"] = self.anchor_type
        if self.source_ids:
            d["source_ids"] = list(self.source_ids)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Anchor:
        return cls(
            id=data["id"],
            payload=data["payload"],
            timestamp=data["timestamp"],
            meta=data.get("meta", {}),
            anchor_type=data.get("anchor_type", "handoff"),
            source_ids=tuple(data.get("source_ids", ())),
        )
