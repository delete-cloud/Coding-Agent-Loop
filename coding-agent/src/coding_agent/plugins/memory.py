"""MemoryPlugin — Grounding + finish_action memory management.

Two modes:
  - Grounding (build_context): Automatically injects relevant memories
    as system messages before each turn.
  - finish_action (on_turn_end): Forces structured MemoryRecord production
    at the end of every turn for persistent learning.

Innovation over Bub: Two-layer memory (near-term compacted + long-term raw),
importance scoring, tag extraction.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from agentkit.directive.types import MemoryRecord
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class MemoryPlugin:
    """Plugin implementing memory management via grounding + finish_action."""

    state_key = "memory"

    def __init__(self, max_grounding: int = 5) -> None:
        self._max_grounding = max_grounding
        self._memories: list[dict[str, Any]] = []
        self._topic_file_tags: set[str] = set()

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "build_context": self.build_context,
            "on_turn_end": self.on_turn_end,
            "on_checkpoint": self.on_checkpoint,
            "mount": self.do_mount,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Initialize memory state."""
        return {"memories": self._memories}

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        """Cache current topic's file tags for scoped recall."""
        if ctx is None:
            return
        entries = (
            ctx.tape.windowed_entries()
            if hasattr(ctx.tape, "windowed_entries")
            else list(ctx.tape)
        )
        files: set[str] = set()
        for entry in entries:
            if entry.kind == "tool_call":
                args = entry.payload.get("arguments")
                if isinstance(args, dict):
                    for key in ("path", "file", "filename", "file_path"):
                        val = args.get(key, "")
                        if val and isinstance(val, str):
                            files.add(val)
        self._topic_file_tags = files

    def build_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Grounding mode: inject relevant memories as system messages.

        If topic file tags are available, filter memories to those with
        overlapping tags. Falls back to importance-sorted top-N otherwise.
        """
        if not self._memories:
            return []

        if self._topic_file_tags:
            relevant = [
                m
                for m in self._memories
                if self._tags_overlap(m.get("tags", []), self._topic_file_tags)
            ]
            if relevant:
                sorted_memories = sorted(
                    relevant, key=lambda m: m.get("importance", 0.5), reverse=True
                )
            else:
                sorted_memories = sorted(
                    self._memories, key=lambda m: m.get("importance", 0.5), reverse=True
                )
        else:
            sorted_memories = sorted(
                self._memories, key=lambda m: m.get("importance", 0.5), reverse=True
            )

        top = sorted_memories[: self._max_grounding]

        grounding_messages = []
        for mem in top:
            content = f"[Memory] {mem['summary']}"
            if mem.get("tags"):
                content += f" (tags: {', '.join(mem['tags'])})"
            grounding_messages.append({"role": "system", "content": content})

        return grounding_messages

    def _tags_overlap(self, memory_tags: list[str], topic_files: set[str]) -> bool:
        """Check if any memory tag overlaps with topic file paths."""
        for tag in memory_tags:
            if tag in topic_files:
                return True
        return False

    def on_turn_end(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> MemoryRecord | None:
        """finish_action: extract a structured memory from the turn.

        Analyzes the tape to produce a MemoryRecord with:
          - summary: What happened in this turn
          - tags: Extracted topic tags
          - importance: Heuristic score (0-1)
        """
        if tape is None or len(tape) == 0:
            return None

        entries = list(tape)
        if len(entries) < 2:
            return None

        last_content = None
        for entry in reversed(entries):
            if entry.kind == "message":
                last_content = entry.payload.get("content", "")
                break

        if not last_content:
            return None

        summary = last_content[:200]
        if len(last_content) > 200:
            summary += "..."

        tags = self._extract_tags(entries)

        importance = self._score_importance(entries)

        record = MemoryRecord(
            summary=summary,
            tags=tags,
            importance=importance,
        )

        return record

    def add_memory(self, record: MemoryRecord) -> None:
        self._memories.append(
            {
                "summary": record.summary,
                "tags": record.tags,
                "importance": record.importance,
            }
        )

    def _extract_tags(self, entries: list[Entry]) -> list[str]:
        """Extract topic tags from tape entries."""
        tags: set[str] = set()
        for entry in entries:
            if entry.kind == "tool_call":
                name = entry.payload.get("name", "")
                if name:
                    tags.add(name)
            elif entry.kind == "message":
                content = entry.payload.get("content", "")
                paths = re.findall(r"[\w/]+\.\w+", content)
                for p in paths[:3]:
                    tags.add(p)
        return sorted(tags)[:5]

    def _score_importance(self, entries: list[Entry]) -> float:
        """Score turn importance (0-1) based on complexity heuristics."""
        tool_calls = sum(1 for e in entries if e.kind == "tool_call")
        messages = sum(1 for e in entries if e.kind == "message")

        tool_score = min(tool_calls / 10.0, 0.5)
        msg_score = min(messages / 20.0, 0.3)
        base = 0.2

        return min(base + tool_score + msg_score, 1.0)
