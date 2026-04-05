from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class DoomDetectorPlugin:
    state_key = "doom_detector"

    def __init__(self, threshold: int = 3) -> None:
        self._threshold = threshold

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"on_checkpoint": self.on_checkpoint}

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        if ctx is None:
            return

        tool_calls = self._current_turn_tool_calls(ctx)
        if not tool_calls:
            ctx.plugin_states[self.state_key] = {"doom_detected": False}
            return

        consecutive = 1
        max_consecutive = 1
        prev_hash = self._hash_call(tool_calls[-1])

        for entry in reversed(tool_calls[:-1]):
            h = self._hash_call(entry)
            if h == prev_hash:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                break

        doom_detected = max_consecutive >= self._threshold

        state: dict[str, Any] = {"doom_detected": doom_detected}
        if doom_detected:
            name = tool_calls[-1].payload.get("name", "unknown")
            state["reason"] = (
                f"doom_loop: {max_consecutive} consecutive identical "
                f"'{name}' calls (threshold={self._threshold})"
            )

        ctx.plugin_states[self.state_key] = state

    def _current_turn_tool_calls(self, ctx: Any) -> list[Any]:
        entries = list(ctx.tape.snapshot())
        turn_start = 0
        for index in range(len(entries) - 1, -1, -1):
            entry = entries[index]
            if entry.kind == "message" and entry.payload.get("role") == "user":
                turn_start = index + 1
                break
        return [entry for entry in entries[turn_start:] if entry.kind == "tool_call"]

    def _hash_call(self, entry: Any) -> str:
        payload = entry.payload
        key = {
            "name": payload.get("name", ""),
            "arguments": payload.get("arguments", {}),
        }
        return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
