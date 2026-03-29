"""ContextBuilder — assembles LLM messages from tape entries + grounding."""

from __future__ import annotations

from typing import Any

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class ContextBuilder:
    """Builds LLM message lists from tape entries."""

    def __init__(self, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt

    def build(
        self,
        tape: Tape,
        grounding: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        for entry in tape:
            msg = self._entry_to_message(entry)
            if msg is not None:
                messages.append(msg)

        if grounding:
            last_user_idx = None
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break
            if last_user_idx is not None:
                for j, g in enumerate(grounding):
                    messages.insert(last_user_idx + j, g)
            else:
                messages.extend(grounding)

        system = {"role": "system", "content": self._system_prompt}
        return [system] + messages

    def _entry_to_message(self, entry: Entry) -> dict[str, Any] | None:
        if entry.kind == "message":
            return {
                "role": entry.payload.get("role", "user"),
                "content": entry.payload.get("content", ""),
            }
        elif entry.kind == "tool_call":
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": entry.payload.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": entry.payload.get("name", ""),
                            "arguments": entry.payload.get("arguments", {}),
                        },
                    }
                ],
            }
        elif entry.kind == "tool_result":
            return {
                "role": "tool",
                "tool_call_id": entry.payload.get("tool_call_id", ""),
                "content": entry.payload.get("content", ""),
            }
        elif entry.kind == "anchor":
            return {
                "role": "system",
                "content": entry.payload.get("content", ""),
            }
        elif entry.kind == "event":
            return None
        return None
