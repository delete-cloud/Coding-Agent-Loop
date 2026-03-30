"""SummarizerPlugin — context window management via rule-based summarization.

When tape exceeds max_entries, older entries are compressed into a summary anchor.
Recent entries (keep_recent) are always preserved verbatim.

Note: LLM-based summarization can be added later as an enhancement.
For V1, we use rule-based truncation with anchor insertion.
"""

from __future__ import annotations

from typing import Any, Callable

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class SummarizerPlugin:
    """Plugin implementing resolve_context_window hook."""

    state_key = "summarizer"

    def __init__(
        self,
        max_entries: int = 100,
        keep_recent: int = 10,
    ) -> None:
        self._max_entries = max_entries
        self._keep_recent = keep_recent

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"resolve_context_window": self.resolve_context_window}

    def resolve_context_window(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> tuple[int, Entry] | None:
        """Determine context window boundaries.

        Strategy:
        1. If tape has topic_finalized anchors and exceeds max_entries,
           fold at the last topic_finalized boundary.
        2. Otherwise, fall back to entry-count truncation (keep_recent).

        Returns (window_start_index, summary_anchor_entry) or None.
        """
        if tape is None:
            return None

        visible = (
            tape.windowed_entries() if hasattr(tape, "windowed_entries") else list(tape)
        )
        if len(visible) <= self._max_entries:
            return None

        # Strategy 1: find the last topic_finalized anchor
        last_finalized_idx = self._find_last_finalized(visible)
        if last_finalized_idx is not None:
            split_point = last_finalized_idx + 1
            old_entries = visible[:split_point]
            summary_anchor = self._build_topic_summary(old_entries)
            return (split_point, summary_anchor)

        # Strategy 2: fallback to entry-count truncation
        split_point = len(visible) - self._keep_recent
        old_entries = visible[:split_point]
        summary_anchor = self._build_entry_summary(old_entries)
        return (split_point, summary_anchor)

    def _find_last_finalized(self, entries: list[Entry]) -> int | None:
        """Find index of the last topic_finalized anchor in entries."""
        for i in range(len(entries) - 1, -1, -1):
            if (
                entries[i].kind == "anchor"
                and entries[i].meta.get("anchor_type") == "topic_finalized"
            ):
                return i
        return None

    def _build_topic_summary(self, old_entries: list[Entry]) -> Entry:
        """Build a handoff anchor summarizing folded topic entries."""
        topic_ids = []
        for e in old_entries:
            tid = e.meta.get("topic_id")
            if tid and tid not in topic_ids:
                topic_ids.append(tid)

        files: list[str] = []
        for e in old_entries:
            if e.meta.get("anchor_type") == "topic_finalized":
                files.extend(e.meta.get("files", []))

        topic_count = len(topic_ids) or 1
        summary_text = f"[Summarized {len(old_entries)} entries from {topic_count} completed topic(s)]"
        if files:
            summary_text += f"\nFiles involved: {', '.join(sorted(set(files))[:10])}"

        return Entry(
            kind="anchor",
            payload={"content": summary_text},
            meta={
                "anchor_type": "handoff",
                "source_entry_count": len(old_entries),
                "folded_topics": topic_ids,
                "prefix": "Context Summary",
            },
        )

    def _build_entry_summary(self, old_entries: list[Entry]) -> Entry:
        """Build a handoff anchor from raw entry list (fallback)."""
        summary_parts = []
        for entry in old_entries:
            if entry.kind == "message":
                role = entry.payload.get("role", "?")
                content = entry.payload.get("content", "")
                preview = content[:100] + "..." if len(content) > 100 else content
                summary_parts.append(f"[{role}] {preview}")
            elif entry.kind == "tool_call":
                name = entry.payload.get("name", "?")
                summary_parts.append(f"[tool_call] {name}")
            elif entry.kind == "tool_result":
                summary_parts.append("[tool_result] ...")

        summary_text = f"[Summarized {len(old_entries)} earlier entries]\n" + "\n".join(
            summary_parts[-10:]
        )

        return Entry(
            kind="anchor",
            payload={"content": summary_text},
            meta={
                "anchor_type": "handoff",
                "source_entry_count": len(old_entries),
                "prefix": "Context Summary",
            },
        )

    def summarize_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[Entry] | None:
        """Legacy summarize_context hook — kept for backward compatibility."""
        if tape is None:
            return None

        entries = list(tape)
        if len(entries) <= self._max_entries:
            return None

        split_point = len(entries) - self._keep_recent
        old_entries = entries[:split_point]
        recent_entries = entries[split_point:]

        summary_parts = []
        for entry in old_entries:
            if entry.kind == "message":
                role = entry.payload.get("role", "?")
                content = entry.payload.get("content", "")
                preview = content[:100] + "..." if len(content) > 100 else content
                summary_parts.append(f"[{role}] {preview}")
            elif entry.kind == "tool_call":
                name = entry.payload.get("name", "?")
                summary_parts.append(f"[tool_call] {name}")
            elif entry.kind == "tool_result":
                summary_parts.append("[tool_result] ...")

        summary_text = f"[Summarized {len(old_entries)} earlier entries]\n" + "\n".join(
            summary_parts[-10:]
        )

        anchor = Entry(
            kind="anchor",
            payload={"content": summary_text},
        )

        return [anchor] + recent_entries
