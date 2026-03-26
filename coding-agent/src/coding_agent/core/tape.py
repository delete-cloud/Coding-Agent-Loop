"""Tape: Append-only JSONL storage with fork/merge support."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

# Entry kind type
EntryKind = Literal["message", "tool_call", "tool_result", "anchor", "event"]


@dataclass(frozen=True)
class Entry:
    """Immutable entry in the tape.
    
    Attributes:
        id: Sequential ID (1-indexed)
        kind: Type of entry
        payload: Entry data
        timestamp: ISO format timestamp
    """
    id: int
    kind: EntryKind
    payload: dict
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def message(cls, role: str, content: str, id: int | None = None) -> Entry:
        """Create a message entry."""
        return cls(
            id=id or 0,
            kind="message",
            payload={"role": role, "content": content},
        )

    @classmethod
    def anchor(cls, name: str, state: dict, id: int | None = None) -> Entry:
        """Create an anchor entry."""
        return cls(
            id=id or 0,
            kind="anchor",
            payload={"name": name, "state": state},
        )

    @classmethod
    def tool_call(cls, name: str, arguments: dict, id: int | None = None) -> Entry:
        """Create a tool_call entry."""
        return cls(
            id=id or 0,
            kind="tool_call",
            payload={"name": name, "arguments": arguments},
        )

    @classmethod
    def tool_result(cls, name: str, result: object, id: int | None = None) -> Entry:
        """Create a tool_result entry."""
        return cls(
            id=id or 0,
            kind="tool_result",
            payload={"name": name, "result": result},
        )

    @classmethod
    def event(cls, type: str, data: dict, id: int | None = None) -> Entry:
        """Create an event entry."""
        return cls(
            id=id or 0,
            kind="event",
            payload={"type": type, "data": data},
        )

    def to_dict(self) -> dict:
        """Serialize entry to dict."""
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Entry:
        """Deserialize entry from dict."""
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=data["payload"],
            timestamp=data["timestamp"],
        )


class Tape:
    """Append-only JSONL storage with fork/merge support.
    
    Attributes:
        path: Path to JSONL file (None for in-memory only)
        _entries: List of entries
        _next_id: Next entry ID to assign
    """

    def __init__(self, path: Path | None = None):
        """Initialize tape.
        
        Args:
            path: Path to JSONL file, or None for in-memory tape
        """
        self.path = path
        self._entries: list[Entry] = []
        self._next_id = 1
        
        if path is not None and path.exists():
            self._load()

    @classmethod
    def create(cls, tape_dir: Path) -> Tape:
        """Create a new tape with UUID filename.
        
        Args:
            tape_dir: Directory to store the tape file
            
        Returns:
            New Tape instance
        """
        tape_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4()}.jsonl"
        path = tape_dir / filename
        return cls(path)

    def _load(self) -> None:
        """Load entries from existing JSONL file."""
        if self.path is None or not self.path.exists():
            return
            
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = Entry.from_dict(data)
                self._entries.append(entry)
                self._next_id = max(self._next_id, entry.id + 1)

    def _append_to_file(self, entry: Entry) -> None:
        """Append a single entry to the JSONL file."""
        if self.path is None:
            return
            
        # Ensure parent directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        # Append atomically
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    def append(self, kind: EntryKind, payload: dict) -> Entry:
        """Append a new entry to the tape.
        
        Args:
            kind: Type of entry
            payload: Entry data
            
        Returns:
            The created entry
        """
        entry = Entry(
            id=self._next_id,
            kind=kind,
            payload=payload,
        )
        self._entries.append(entry)
        self._next_id += 1
        self._append_to_file(entry)
        return entry

    def entries(self, after_anchor: Entry | None = None) -> list[Entry]:
        """Get entries, optionally filtered from an anchor point.
        
        Args:
            after_anchor: If provided, returns entries from this anchor onwards
            
        Returns:
            List of entries (copies)
        """
        if after_anchor is None:
            return deepcopy(self._entries)
        
        # Find the index of the anchor
        for i, entry in enumerate(self._entries):
            if entry.id == after_anchor.id:
                return deepcopy(self._entries[i:])
        
        # Anchor not found, return all
        return deepcopy(self._entries)

    def handoff(self, name: str, state: dict) -> Entry:
        """Create an anchor entry for phase handoff.
        
        Args:
            name: Anchor name
            state: State to checkpoint
            
        Returns:
            The created anchor entry
        """
        return self.append("anchor", {"name": name, "state": state})

    def fork(self) -> Tape:
        """Create an in-memory copy of this tape.
        
        Returns:
            New Tape with copied entries but no path
        """
        forked = Tape(path=None)
        forked._entries = deepcopy(self._entries)
        forked._next_id = self._next_id
        return forked

    def merge(self, forked: Tape) -> None:
        """Merge entries from a forked tape.
        
        Adds entries from forked that are not present in this tape.
        For divergent histories (both added entries), entries are appended
        with new sequential IDs.
        
        Args:
            forked: The forked tape to merge from
        """
        # Build set of existing entry identifiers (kind + payload)
        existing_signatures = {
            (e.kind, json.dumps(e.payload, sort_keys=True))
            for e in self._entries
        }
        
        # Find entries from forked that are not in self
        new_entries_from_forked = []
        for entry in forked._entries:
            sig = (entry.kind, json.dumps(entry.payload, sort_keys=True))
            if sig not in existing_signatures:
                new_entries_from_forked.append(entry)
                existing_signatures.add(sig)
        
        # Append new entries with new sequential IDs
        for entry in new_entries_from_forked:
            new_entry = Entry(
                id=self._next_id,
                kind=entry.kind,
                payload=entry.payload,
                timestamp=entry.timestamp,
            )
            self._entries.append(new_entry)
            self._append_to_file(new_entry)
            self._next_id += 1
