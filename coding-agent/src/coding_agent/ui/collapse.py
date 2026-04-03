from __future__ import annotations

import time
from dataclasses import dataclass, field

_COLLAPSIBLE_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "grep_search",
        "glob_files",
        "grep",
        "glob",
    }
)
_SEARCH_TOOLS: frozenset[str] = frozenset({"grep_search", "grep"})
_READ_TOOLS: frozenset[str] = frozenset({"file_read"})
_LIST_TOOLS: frozenset[str] = frozenset({"glob_files", "glob"})


def is_collapsible(tool_name: str) -> bool:
    return tool_name in _COLLAPSIBLE_TOOLS


def classify_tool(tool_name: str) -> str:
    if tool_name in _SEARCH_TOOLS:
        return "search"
    if tool_name in _READ_TOOLS:
        return "read"
    if tool_name in _LIST_TOOLS:
        return "list"
    return "unknown"


@dataclass
class CollapseGroup:
    search_count: int = 0
    read_count: int = 0
    list_count: int = 0
    read_file_paths: list[str] = field(default_factory=list)
    search_patterns: list[str] = field(default_factory=list)
    pending_call_ids: set[str] = field(default_factory=set)
    start_time: float = field(default_factory=time.perf_counter)
    _has_error: bool = False

    def add_tool_call(
        self, call_id: str, tool_name: str, args: dict[str, object]
    ) -> None:
        category = classify_tool(tool_name)
        if category == "search":
            self.search_count += 1
            pattern = args.get("pattern") or args.get("regex")
            if pattern:
                self.search_patterns.append(str(pattern))
        elif category == "read":
            self.read_count += 1
            path = args.get("path") or args.get("file_path")
            if path:
                self.read_file_paths.append(str(path))
        elif category == "list":
            self.list_count += 1
        self.pending_call_ids.add(call_id)

    def add_tool_result(self, call_id: str, is_error: bool = False) -> None:
        self.pending_call_ids.discard(call_id)
        if is_error:
            self._has_error = True

    def has_call(self, call_id: str) -> bool:
        return call_id in self.pending_call_ids

    @property
    def is_empty(self) -> bool:
        return self.search_count == 0 and self.read_count == 0 and self.list_count == 0

    @property
    def has_error(self) -> bool:
        return self._has_error

    @property
    def duration(self) -> float:
        return time.perf_counter() - self.start_time

    def summary_text(self) -> str:
        parts: list[str] = []
        if self.search_count > 0:
            noun = "pattern" if self.search_count == 1 else "patterns"
            parts.append(f"Searched for {self.search_count} {noun}")
        if self.read_count > 0:
            noun = "file" if self.read_count == 1 else "files"
            parts.append(f"read {self.read_count} {noun}")
        if self.list_count > 0:
            noun = "pattern" if self.list_count == 1 else "patterns"
            parts.append(f"listed {self.list_count} {noun}")
        if not parts:
            return ""
        text = ", ".join(parts)
        return text[0].upper() + text[1:]
