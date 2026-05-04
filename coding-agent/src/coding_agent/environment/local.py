from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentkit.environment import FileTools, WorkspaceSummary


class LocalEnvironment:
    """Local filesystem and process environment for coding-agent tools."""

    def __init__(self, workspace_root: Path | str) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()

    @property
    def kind(self) -> str:
        return "local"

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def tool_config(self) -> dict[str, Any]:
        return {"workspace_root": str(self._workspace_root)}

    def workspace_summary(self) -> WorkspaceSummary:
        root = str(self._workspace_root)
        return WorkspaceSummary(display_name=root, default_cwd=root, local_root=root)

    def build_file_tools(self) -> FileTools:
        from coding_agent.tools.file_ops import build_file_tools

        return build_file_tools(self._workspace_root)

    def build_file_patch_tool(self) -> Callable[[str, str], str]:
        from coding_agent.tools.file_patch_tool import build_file_patch_tool

        return build_file_patch_tool(self._workspace_root)

    def build_shell_tool(self) -> Callable[..., Any]:
        from coding_agent.tools.shell import bash_run

        return bash_run
