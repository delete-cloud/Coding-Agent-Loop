from __future__ import annotations

import uuid
from typing import Any, Callable

from agentkit.tape.anchor import Anchor
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class TopicPlugin:
    state_key = "topic"

    def __init__(
        self,
        overlap_threshold: float = 0.2,
        min_entries_before_detect: int = 4,
    ) -> None:
        self._overlap_threshold = overlap_threshold
        self._min_entries = min_entries_before_detect
        self._current_topic_id: str | None = None
        self._current_topic_files: set[str] = set()
        self._topic_count: int = 0

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "on_checkpoint": self.on_checkpoint,
            "on_session_event": self.on_session_event,
            "mount": self.do_mount,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "current_topic_id": self._current_topic_id,
            "topic_count": self._topic_count,
        }

    def on_session_event(
        self, event_type: str = "", payload: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        return

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        if ctx is None:
            return

        runtime = kwargs.get("runtime")

        tape: Tape = ctx.tape
        entries = (
            tape.windowed_entries() if hasattr(tape, "windowed_entries") else list(tape)
        )

        if len(entries) < self._min_entries:
            if self._current_topic_id is None:
                self._start_topic(tape, entries, runtime=runtime)
            self._sync_state(ctx)
            return

        recent_files = self._extract_files_from_recent(entries)

        if self._current_topic_id is None:
            self._start_topic(tape, entries, runtime=runtime)
            self._current_topic_files = recent_files
            self._sync_state(ctx)
            return

        if not recent_files:
            self._sync_state(ctx)
            return

        if not self._current_topic_files:
            self._current_topic_files = recent_files
            self._sync_state(ctx)
            return

        overlap = len(recent_files & self._current_topic_files)
        total = max(len(self._current_topic_files), 1)
        overlap_ratio = overlap / total

        if overlap_ratio < self._overlap_threshold:
            self._end_topic(tape, runtime=runtime)
            self._start_topic(tape, entries, runtime=runtime)
            self._current_topic_files = recent_files
        else:
            self._current_topic_files |= recent_files

        self._sync_state(ctx)

    def _sync_state(self, ctx: Any) -> None:
        ctx.plugin_states[self.state_key] = {
            "current_topic_id": self._current_topic_id,
            "topic_count": self._topic_count,
        }

    def _start_topic(
        self, tape: Tape, entries: list[Entry], runtime: Any = None
    ) -> None:
        self._current_topic_id = f"topic-{uuid.uuid4().hex[:8]}"
        self._topic_count += 1

        first_user_msg = ""
        for entry in reversed(entries):
            if entry.kind == "message" and entry.payload.get("role") == "user":
                first_user_msg = entry.payload.get("content", "")[:100]
                break

        tape.append(
            Anchor(
                anchor_type="topic_start",
                payload={"content": first_user_msg or f"Topic #{self._topic_count}"},
                meta={
                    "topic_id": self._current_topic_id,
                    "topic_number": self._topic_count,
                    "prefix": "Topic Start",
                },
            )
        )

        if runtime is not None and hasattr(runtime, "notify"):
            runtime.notify(
                "on_session_event",
                event_type="topic_start",
                payload={
                    "topic_id": self._current_topic_id,
                    "topic_number": self._topic_count,
                    "label": first_user_msg or f"Topic #{self._topic_count}",
                },
            )

    def _end_topic(self, tape: Tape, runtime: Any = None) -> None:
        if self._current_topic_id is None:
            return

        file_list = sorted(self._current_topic_files)[:10]
        summary = (
            f"Topic involved files: {', '.join(file_list)}"
            if file_list
            else "Topic completed"
        )

        # "skip": True tells ContextBuilder to omit this anchor from LLM messages.
        # topic_end is a structural boundary (fold_boundary==True on the Anchor),
        # not content the model should see.  The skip meta provides the same
        # suppression for older code paths that check meta directly.
        tape.append(
            Anchor(
                anchor_type="topic_end",
                payload={"content": summary},
                meta={
                    "topic_id": self._current_topic_id,
                    "files": file_list,
                    "skip": True,
                },
            )
        )

        if runtime is not None and hasattr(runtime, "notify"):
            runtime.notify(
                "on_session_event",
                event_type="topic_end",
                payload={
                    "topic_id": self._current_topic_id,
                    "files": file_list,
                },
            )

        self._current_topic_id = None
        self._current_topic_files = set()

    def _extract_files_from_recent(self, entries: list[Entry]) -> set[str]:
        files: set[str] = set()

        last_user_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            if (
                entries[i].kind == "message"
                and entries[i].payload.get("role") == "user"
            ):
                last_user_idx = i
                break

        for entry in entries[last_user_idx:]:
            if entry.kind == "tool_call":
                tool_calls = entry.payload.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            self._extract_files_from_tool_args(
                                tc.get("arguments"), files
                            )
                else:
                    self._extract_files_from_tool_args(
                        entry.payload.get("arguments"), files
                    )

        return files

    def _extract_files_from_tool_args(self, args: Any, files: set[str]) -> None:
        if not isinstance(args, dict):
            return
        for key in ("path", "file", "filename", "file_path"):
            val = args.get(key, "")
            if val and isinstance(val, str):
                files.add(val)

    @property
    def current_topic_id(self) -> str | None:
        return self._current_topic_id

    @property
    def topic_count(self) -> int:
        return self._topic_count
