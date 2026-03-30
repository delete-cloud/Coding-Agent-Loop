"""Tape module for persistent conversation logging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Tape:
    """Manages persistent logging of conversation events.
    
    The tape is stored as a JSON Lines (.jsonl) file where each line
    represents an event in the conversation.
    """
    
    def __init__(self, path: Path):
        """Initialize the tape.
        
        Args:
            path: Path to the JSONL file.
        """
        self.path = path
        self._ensure_file_exists()
    
    def _ensure_file_exists(self) -> None:
        """Ensure the tape file exists."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
    
    def append(self, event: dict[str, Any]) -> None:
        """Append an event to the tape.
        
        Args:
            event: The event to append.
        """
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")
    
    def read_all(self) -> list[dict[str, Any]]:
        """Read all events from the tape.
        
        Returns:
            List of all events in chronological order.
        """
        events = []
        if not self.path.exists():
            return events
        
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
    
    def clear(self) -> None:
        """Clear all events from the tape."""
        if self.path.exists():
            self.path.unlink()
        self._ensure_file_exists()
