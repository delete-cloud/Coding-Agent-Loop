"""ContextBuilder — assembles LLM messages from tape entries + grounding."""

from __future__ import annotations

import json
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
        entries: list[Entry] | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        entries = entries if entries is not None else list(tape)
        index = 0
        while index < len(entries):
            entry = entries[index]
            if entry.kind == "tool_call":
                tool_calls: list[dict[str, Any]] = []
                role = entry.payload.get("role", "assistant")

                while index < len(entries) and entries[index].kind == "tool_call":
                    current = entries[index]
                    current_calls = current.payload.get("tool_calls")
                    if isinstance(current_calls, list):
                        tool_calls.extend(current_calls)
                    else:
                        tool_calls.append(
                            {
                                "id": current.payload.get("id", ""),
                                "name": current.payload.get("name", ""),
                                "arguments": current.payload.get("arguments", {}),
                            }
                        )
                    index += 1

                tool_call_msg: dict[str, Any] = {
                    "role": role,
                    "content": None,
                    "tool_calls": [
                        self._tool_call_to_message(tool_call)
                        for tool_call in tool_calls
                    ],
                }

                # Merge with preceding assistant text to avoid adjacent
                # same-role messages (Anthropic API rejects those).
                if (
                    messages
                    and messages[-1].get("role") == role
                    and messages[-1].get("content")
                    and "tool_calls" not in messages[-1]
                ):
                    tool_call_msg["content"] = messages[-1]["content"]
                    messages[-1] = tool_call_msg
                else:
                    messages.append(tool_call_msg)
                continue

            msg = self._entry_to_message(entry)
            if msg is not None:
                messages.append(msg)
            index += 1

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
            tool_calls = entry.payload.get("tool_calls")
            if not isinstance(tool_calls, list):
                tool_calls = [
                    {
                        "id": entry.payload.get("id", ""),
                        "name": entry.payload.get("name", ""),
                        "arguments": entry.payload.get("arguments", {}),
                    }
                ]

            return {
                "role": entry.payload.get("role", "assistant"),
                "content": None,
                "tool_calls": [self._tool_call_to_message(tc) for tc in tool_calls],
            }
        elif entry.kind == "tool_result":
            return {
                "role": "tool",
                "tool_call_id": entry.payload.get("tool_call_id", ""),
                "content": entry.payload.get("content", ""),
            }
        elif entry.kind == "anchor":
            if entry.meta.get("skip"):
                return None

            content = entry.payload.get("content", "")
            prefix = entry.meta.get("prefix")
            if prefix:
                content = f"[{prefix}] {content}"

            return {"role": "system", "content": content}
        elif entry.kind == "event":
            return None
        return None

    def _tool_call_to_message(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        arguments = tool_call.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)

        return {
            "id": tool_call.get("id", ""),
            "type": "function",
            "function": {
                "name": tool_call.get("name", ""),
                "arguments": arguments,
            },
        }
