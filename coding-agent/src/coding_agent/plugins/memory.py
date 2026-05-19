"""MemoryPlugin — Grounding + finish_action memory management.

Two modes:
  - Grounding (build_context): Automatically injects relevant memories
    as system messages before each turn.
  - finish_action (on_turn_end): Forces structured MemoryRecord production
    at the end of every turn for persistent learning.

Innovation over Bub: Two-layer memory (near-term compacted + long-term raw),
importance scoring, tag extraction.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnannotatedClassAttribute=false, reportUnusedParameter=false

from __future__ import annotations

import re
from pathlib import PurePosixPath
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
        self._working_memories: list[dict[str, Any]] = []
        self._topic_file_tags: set[str] = set()
        self._storage_plugin: Any | None = None
        self._session_id: str | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "build_context": self.build_context,
            "on_turn_end": self.on_turn_end,
            "on_checkpoint": self.on_checkpoint,
            "on_session_event": self.on_session_event,
            "mount": self.do_mount,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Initialize memory state."""
        ctx = kwargs.get("ctx")
        if ctx is not None:
            self._session_id = getattr(ctx, "session_id", None)
            storage_state = getattr(ctx, "plugin_states", {}).get("storage", {})
            if isinstance(storage_state, dict):
                self._storage_plugin = storage_state.get("plugin")

        if self._storage_plugin is not None and self._session_id is not None:
            persisted = self._storage_plugin.load_memory_records(self._session_id)
            self._memories = [
                self._apply_importance_decay(record) for record in persisted
            ]
            self._storage_plugin.replace_memory_records(
                self._session_id, self._memories
            )

        return {
            "memories": self._memories,
            "working_memories": self._working_memories,
        }

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

    def on_session_event(
        self, event_type: str = "", payload: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        payload = payload or {}
        if event_type != "topic_end":
            return

        topic_id = payload.get("topic_id", "")
        files = payload.get("files", [])
        summary = payload.get("summary", "")
        if not topic_id:
            return

        if not isinstance(summary, str) or not summary:
            summary = f"Topic {topic_id} completed"

        compacted = self._compact_topic_memory(summary=summary, files=files)
        self._memories.append(compacted)
        self._working_memories.clear()
        self._persist_long_term_memory(compacted)

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
        tags = list(record.tags)
        self._working_memories.append(
            {
                "summary": record.summary,
                "tags": tags,
                "importance": record.importance,
                "evidence": _evidence_refs_from_tags(tags),
            }
        )

    def _compact_topic_memory(
        self, summary: str, files: list[Any] | Any
    ) -> dict[str, Any]:
        tags: set[str] = set()
        if isinstance(files, list):
            for file_path in files:
                if isinstance(file_path, str) and file_path:
                    tags.add(file_path)

        for memory in self._working_memories:
            for tag in memory.get("tags", []):
                if isinstance(tag, str) and tag:
                    tags.add(tag)

        if self._working_memories:
            total_importance = sum(
                float(memory["importance"]) for memory in self._working_memories
            )
            importance = round(total_importance / len(self._working_memories), 4)
        else:
            importance = 0.5

        sorted_tags = sorted(tags)
        return {
            "summary": summary,
            "tags": sorted_tags,
            "importance": importance,
            "evidence": _merge_evidence_refs(
                [
                    *_evidence_refs_from_tags(sorted_tags),
                    *[
                        evidence
                        for memory in self._working_memories
                        for evidence in _normalize_evidence_refs(
                            memory.get("evidence", [])
                        )
                    ],
                ]
            ),
        }

    def _persist_long_term_memory(self, memory: dict[str, Any]) -> None:
        if self._storage_plugin is None or self._session_id is None:
            return
        self._storage_plugin.append_memory_record(self._session_id, memory)

    def _apply_importance_decay(self, memory: dict[str, Any]) -> dict[str, Any]:
        importance = memory.get("importance")
        if not isinstance(importance, (int, float)):
            raise ValueError("persisted memory missing numeric importance")

        tags = _normalize_tags(memory.get("tags", []))
        return {
            "summary": memory.get("summary", ""),
            "tags": tags,
            "importance": round(float(importance) * 0.9, 4),
            "evidence": _normalize_evidence_refs(memory.get("evidence", [])),
        }

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


def _normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [tag for tag in value if isinstance(tag, str) and tag]


def _evidence_refs_from_tags(tags: list[str]) -> list[dict[str, Any]]:
    return _merge_evidence_refs(
        [
            {
                "kind": "repo_file",
                "source_id": tag,
                "label": tag,
                "repo_path": tag,
            }
            for tag in tags
            if _is_repo_path_tag(tag)
        ]
    )


def _is_repo_path_tag(tag: str) -> bool:
    path = PurePosixPath(tag)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and bool(path.name)
        and "." in path.name
        and not any(char.isspace() for char in tag)
    )


def _normalize_evidence_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    refs: list[dict[str, Any]] = []
    for raw_ref in value:
        if not isinstance(raw_ref, dict):
            continue
        kind = raw_ref.get("kind")
        source_id = raw_ref.get("source_id")
        label = raw_ref.get("label")
        if not all(isinstance(item, str) and item for item in (kind, source_id, label)):
            continue

        ref: dict[str, Any] = {
            "kind": kind,
            "source_id": source_id,
            "label": label,
        }
        for key in (
            "repo_path",
            "chunk_id",
            "test_node_id",
            "command_label",
            "session_id",
            "tape_entry_id",
        ):
            value_for_key = raw_ref.get(key)
            if isinstance(value_for_key, str) and value_for_key:
                ref[key] = value_for_key
        line_start = _positive_int(raw_ref.get("line_start"))
        line_end = _positive_int(raw_ref.get("line_end"))
        if line_start is not None and line_end is not None and line_end < line_start:
            line_start = None
            line_end = None
        if line_start is not None:
            ref["line_start"] = line_start
        if line_end is not None:
            ref["line_end"] = line_end
        refs.append(ref)
    return _merge_evidence_refs(refs)


def _positive_int(value: Any) -> int | None:
    if type(value) is int and value > 0:
        return value
    return None


def _merge_evidence_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        kind = ref.get("kind")
        source_id = ref.get("source_id")
        if not isinstance(kind, str) or not isinstance(source_id, str):
            continue
        key = (kind, source_id)
        if key in seen:
            continue
        seen.add(key)
        merged.append(ref)
    return merged
