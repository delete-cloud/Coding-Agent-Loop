from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


@dataclass
class TapeView:
    entries: list[Entry]
    source_tape_id: str
    window_start: int = 0
    # Pre-computed for future use by context windowing strategies.
    # Currently populated by from_tape() but not consumed downstream.
    anchor_ids: tuple[str, ...] = ()

    @classmethod
    def from_tape(cls, tape: Tape) -> TapeView:
        if tape.window_start > 0:
            windowed_entries = tape.windowed_entries()
            handoff_anchors = [
                entry
                for entry in windowed_entries
                if entry.kind == "anchor" and entry.meta.get("is_handoff")
            ]
            other_entries = [
                entry
                for entry in windowed_entries
                if not (entry.kind == "anchor" and entry.meta.get("is_handoff"))
            ]
            entries = handoff_anchors + other_entries
        else:
            entries = list(tape)
        anchor_ids = tuple(e.id for e in entries if e.kind == "anchor")
        return cls(
            entries=entries,
            source_tape_id=tape.tape_id,
            window_start=tape.window_start,
            anchor_ids=anchor_ids,
        )

    @classmethod
    def full(cls, tape: Tape) -> TapeView:
        return cls(
            entries=list(tape),
            source_tape_id=tape.tape_id,
            window_start=0,
        )

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)
