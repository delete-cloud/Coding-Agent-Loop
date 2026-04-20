from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, overload

from agentkit._types import EntryKind
from agentkit.tape.models import Entry


def _scan_window_start(entries: list[Entry]) -> int:
    """Scan entries for the last handoff anchor and return its index as window_start."""
    from agentkit.tape.anchor import Anchor

    window_start = 0
    for i, entry in enumerate(entries):
        if isinstance(entry, Anchor):
            if entry.is_handoff:
                window_start = i
        elif entry.kind == "anchor":
            if entry.meta.get("is_handoff"):
                window_start = i
    return window_start


class Tape:
    def __init__(
        self,
        entries: list[Entry] | None = None,
        tape_id: str | None = None,
        parent_id: str | None = None,
        _window_start: int = 0,
        _persisted_count: int = 0,
    ) -> None:
        self._entries: list[Entry] = list(entries or [])
        self.tape_id: str = tape_id or str(uuid.uuid4())
        self.parent_id: str | None = parent_id
        self._window_start: int = _window_start
        self._persisted_count: int = _persisted_count
        self._lock = Lock()

    @property
    def window_start(self) -> int:
        return self._window_start

    def windowed_entries(self) -> list[Entry]:
        with self._lock:
            return list(self._entries[self._window_start :])

    def snapshot(self) -> tuple[Entry, ...]:
        with self._lock:
            return tuple(self._entries)

    def handoff(self, summary_anchor: Entry, window_start: int | None = None) -> None:
        with self._lock:
            self._entries.append(summary_anchor)
            if window_start is not None:
                self._window_start = window_start
            else:
                self._window_start = len(self._entries) - 1

    def append(self, entry: Entry) -> None:
        with self._lock:
            self._entries.append(entry)

    def filter(self, kind: EntryKind) -> list[Entry]:
        with self._lock:
            return [e for e in self._entries if e.kind == kind]

    def fork(self) -> Tape:
        with self._lock:
            return Tape(
                entries=list(self._entries),
                parent_id=self.tape_id,
                _window_start=self._window_start,
                _persisted_count=self._persisted_count,
            )

    def to_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._entries]

    @classmethod
    def from_list(cls, data: list[dict[str, Any]], **kwargs: Any) -> Tape:
        entries = [Entry.from_dict(d) for d in data]
        if "_window_start" not in kwargs:
            kwargs["_window_start"] = _scan_window_start(entries)
        return cls(entries=entries, **kwargs)

    def save_jsonl(self, path: Path) -> None:
        with self._lock:
            if path.exists() and self._persisted_count > 0:
                mode = "a"
                start = self._persisted_count
            else:
                mode = "w"
                start = 0

            with open(path, mode) as f:
                for entry in self._entries[start:]:
                    f.write(json.dumps(entry.to_dict()) + "\n")

            self._persisted_count = len(self._entries)

    @classmethod
    def load_jsonl(cls, path: Path, **kwargs: Any) -> Tape:
        entries: list[Entry] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(Entry.from_dict(json.loads(line)))
        window_start = _scan_window_start(entries)
        return cls(
            entries=entries,
            _window_start=window_start,
            _persisted_count=len(entries),
            **kwargs,
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @overload
    def __getitem__(self, index: int) -> Entry: ...
    @overload
    def __getitem__(self, index: slice) -> list[Entry]: ...

    def __getitem__(self, index: int | slice) -> Entry | list[Entry]:
        with self._lock:
            return self._entries[index]

    def __iter__(self) -> Iterator[Entry]:
        with self._lock:
            return iter(list(self._entries))
