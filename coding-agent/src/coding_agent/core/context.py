"""Context: assemble LLM-ready messages from tape entries."""

from __future__ import annotations

import json
from typing import Any

from coding_agent.core.tape import Entry, Tape


class Context:
    """Builds a working set of messages from tape entries.

    Strategy for P0 (basic):
    1. Find the most recent anchor → start from there
    2. Convert entries to OpenAI-format messages
    3. Exclude event entries (not useful for LLM reasoning)
    4. Prepend system prompt
    5. Enforce max_tokens budget (approximate, ~4 chars per token)
    """

    # Approximate chars per token for budget estimation
    CHARS_PER_TOKEN = 4

    def __init__(self, max_tokens: int, system_prompt: str):
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self._max_chars = max_tokens * self.CHARS_PER_TOKEN

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count from character count."""
        return len(text) // self.CHARS_PER_TOKEN

    def _message_to_text(self, message: dict[str, Any]) -> str:
        """Extract text content from message for token estimation."""
        parts = []
        if message.get("content"):
            parts.append(str(message["content"]))
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                if tc.get("function", {}).get("name"):
                    parts.append(tc["function"]["name"])
                if tc.get("function", {}).get("arguments"):
                    parts.append(str(tc["function"]["arguments"]))
        if message.get("tool_call_id"):
            parts.append(message["tool_call_id"])
        return " ".join(parts)

    def build_working_set(self, tape: Tape) -> list[dict[str, Any]]:
        """Assemble LLM-ready messages from tape entries.
        
        Messages are truncated if they exceed max_tokens budget.
        System prompt is always preserved. Truncation removes oldest
        non-system messages first.
        """
        messages: list[dict[str, Any]] = []

        # System prompt always first
        system_msg = {"role": "system", "content": self.system_prompt}
        messages.append(system_msg)
        current_chars = len(self.system_prompt)

        # Find the last anchor to start from
        entries = tape.entries()
        start_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].kind == "anchor":
                start_idx = i
                break

        # Convert entries to messages, tracking token budget
        new_messages: list[dict[str, Any]] = []
        for entry in entries[start_idx:]:
            msg = self._entry_to_message(entry)
            if msg is not None:
                msg_text = self._message_to_text(msg)
                msg_chars = len(msg_text)
                
                # Check if adding this message would exceed budget
                if current_chars + msg_chars > self._max_chars:
                    # Budget exceeded - truncate by removing oldest non-system messages
                    # until we can fit this one, or skip if it alone exceeds budget
                    if msg_chars > self._max_chars:
                        # This single message is too large - truncate its content
                        msg = self._truncate_message(msg, self._max_chars - current_chars)
                        if msg:
                            new_messages.append(msg)
                    else:
                        # Remove oldest messages to make room
                        while new_messages and current_chars + msg_chars > self._max_chars:
                            removed = new_messages.pop(0)
                            current_chars -= len(self._message_to_text(removed))
                        new_messages.append(msg)
                        current_chars += msg_chars
                else:
                    new_messages.append(msg)
                    current_chars += msg_chars

        # Combine system message with (possibly truncated) new messages
        return [system_msg] + new_messages

    def _truncate_message(self, message: dict[str, Any], max_chars: int) -> dict[str, Any] | None:
        """Truncate a message to fit within max_chars.
        
        Returns None if message cannot be truncated meaningfully.
        """
        if max_chars <= 0:
            return None
        
        # Truncate content if present
        if message.get("content") and len(str(message["content"])) > max_chars:
            truncated = str(message["content"])[:max_chars - 3] + "..."
            result = dict(message)
            result["content"] = truncated
            return result
        
        # Truncate tool result content
        if message.get("role") == "tool" and message.get("content"):
            content = str(message["content"])
            if len(content) > max_chars:
                truncated = content[:max_chars - 3] + "..."
                result = dict(message)
                result["content"] = truncated
                return result
        
        return message

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
                                "arguments": json.dumps(
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
