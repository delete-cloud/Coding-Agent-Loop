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
    """Plugin implementing summarize_context hook."""

    state_key = "summarizer"

    def __init__(
        self,
        max_entries: int = 100,
        keep_recent: int = 10,
    ) -> None:
        self._max_entries = max_entries
        self._keep_recent = keep_recent

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"summarize_context": self.summarize_context}

    def summarize_context(
        self, tape: Tape | None = None, **kwargs: Any
    ) -> list[Entry] | None:
        """Summarize tape if it exceeds max_entries.

        Returns a new entry list with old entries compressed into an anchor,
        or None if no summarization is needed.
        """
        if tape is None:
            return None

        entries = list(tape)
        if len(entries) <= self._max_entries:
            return None

        # Split into old (to summarize) and recent (to keep)
        split_point = len(entries) - self._keep_recent
        old_entries = entries[:split_point]
        recent_entries = entries[split_point:]

        # Create summary of old entries
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
