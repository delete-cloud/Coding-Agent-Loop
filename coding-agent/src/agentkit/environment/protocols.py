from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

# Tool order: file_read, file_write, file_replace, glob_files, grep_search.
FileTools = tuple[
    Callable[[str], str | dict[str, Any]],
    Callable[[str, str], str],
    Callable[[str, str, str], str],
    Callable[[str, str], str],
    Callable[[str, str, str], str | dict[str, Any]],
]


class Environment(Protocol):
    """Execution substrate for tools in a session."""

    @property
    def kind(self) -> str: ...

    def tool_config(self) -> dict[str, Any]: ...

    def build_file_tools(self) -> FileTools: ...

    def build_file_patch_tool(self) -> Callable[[str, str], str]: ...

    def build_shell_tool(self) -> Callable[..., Any]: ...
