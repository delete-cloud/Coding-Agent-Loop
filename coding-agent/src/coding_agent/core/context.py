"""Context: assemble LLM-ready messages from tape entries."""

from __future__ import annotations

from typing import Any

from coding_agent.core.tape import Entry, Tape


class Context:
    """Builds a working set of messages from tape entries.

    Strategy for P0 (basic):
    1. Find the most recent anchor → start from there
    2. Convert entries to OpenAI-format messages
    3. Exclude event entries (not useful for LLM reasoning)
    4. Prepend system prompt
    """

    def __init__(self, max_tokens: int, system_prompt: str):
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt

    def build_working_set(self, tape: Tape) -> list[dict[str, Any]]:
        """Assemble LLM-ready messages from tape entries."""
        messages: list[dict[str, Any]] = []

        # System prompt always first
        messages.append({"role": "system", "content": self.system_prompt})

        # Find the last anchor to start from
        entries = tape.entries()
        start_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].kind == "anchor":
                start_idx = i
                break

        # Convert entries to messages
        for entry in entries[start_idx:]:
            msg = self._entry_to_message(entry)
            if msg is not None:
                messages.append(msg)

        return messages

    def _entry_to_message(self, entry: Entry) -> dict[str, Any] | None:
        match entry.kind:
            case "message":
                return {
                    "role": entry.payload["role"],
                    "content": entry.payload["content"],
                }
            case "anchor":
                state = entry.payload.get("state", {})
                name = entry.payload.get("name", "checkpoint")
                summary = state.get("summary", f"Phase: {name}")
                return {
                    "role": "system",
                    "content": f"[Checkpoint: {name}] {summary}",
                }
            case "tool_call":
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": entry.payload["call_id"],
                            "type": "function",
                            "function": {
                                "name": entry.payload["tool"],
                                "arguments": __import__("json").dumps(
                                    entry.payload["args"]
                                ),
                            },
                        }
                    ],
                }
            case "tool_result":
                return {
                    "role": "tool",
                    "tool_call_id": entry.payload["call_id"],
                    "content": entry.payload["result"],
                }
            case "event":
                return None  # Events excluded from LLM context
            case _:
                return None
