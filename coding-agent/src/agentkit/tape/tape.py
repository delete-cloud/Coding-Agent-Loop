from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Iterator, overload

from agentkit._types import EntryKind
from agentkit.tape.models import Entry


class Tape:
    def __init__(
        self,
        entries: list[Entry] | None = None,
        tape_id: str | None = None,
        parent_id: str | None = None,
    ) -> None:
        self._entries: list[Entry] = list(entries or [])
        self.tape_id: str = tape_id or str(uuid.uuid4())
        self.parent_id: str | None = parent_id
        self._lock = Lock()

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
            )

    def to_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._entries]

    @classmethod
    def from_list(cls, data: list[dict[str, Any]], **kwargs: Any) -> Tape:
        entries = [Entry.from_dict(d) for d in data]
        return cls(entries=entries, **kwargs)

    def save_jsonl(self, path: Path) -> None:
        with self._lock:
            with open(path, "w") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry.to_dict()) + "\n")

    @classmethod
    def load_jsonl(cls, path: Path, **kwargs: Any) -> Tape:
        entries: list[Entry] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(Entry.from_dict(json.loads(line)))
        return cls(entries=entries, **kwargs)

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
