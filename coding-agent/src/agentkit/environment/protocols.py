from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

# Tool order: file_read, file_write, file_replace, glob_files, grep_search.
FileTools = tuple[
    Callable[[str], str | dict[str, Any]],
    Callable[[str, str], str],
    Callable[[str, str, str], str],
    Callable[[str, str], str],
    Callable[[str, str, str], str | dict[str, Any]],
]


@dataclass(frozen=True)
class WorkspaceSummary:
    """Model-facing workspace identity that does not require a local filesystem."""

    display_name: str
    default_cwd: str | None = None
    local_root: str | None = None


class Environment(Protocol):
    """Execution substrate for tools in a session."""

    @property
    def kind(self) -> str: ...

    def tool_config(self) -> dict[str, Any]: ...

    def workspace_summary(self) -> WorkspaceSummary: ...

    def build_file_tools(self) -> FileTools: ...

    def build_file_patch_tool(self) -> Callable[[str, str], str]: ...

    def build_shell_tool(self) -> Callable[..., Any]: ...
