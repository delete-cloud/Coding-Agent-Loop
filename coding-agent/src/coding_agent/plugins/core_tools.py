from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agentkit.tools import ToolRegistry, ToolSchema


class CoreToolsPlugin:
    state_key = "core_tools"

    def __init__(
        self,
        workspace_root: Path | str = ".",
        planner: Any = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._planner = planner
        self._registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        from coding_agent.tools.file_ops import build_file_tools
        from coding_agent.tools.file_patch_tool import build_file_patch_tool
        from coding_agent.tools.planner import build_planner_tools
        from coding_agent.tools.shell import bash_run
        from coding_agent.tools.subagent_stub import subagent_dispatch

        file_read, file_write, file_replace, glob_files, grep_search = build_file_tools(
            self._workspace_root
        )
        file_patch = build_file_patch_tool(self._workspace_root)
        todo_write, todo_read = build_planner_tools(self._planner)

        for fn in (
            file_read,
            file_write,
            file_replace,
            glob_files,
            grep_search,
            bash_run,
            todo_write,
            todo_read,
            file_patch,
            subagent_dispatch,
        ):
            self._registry.register(fn)

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
        }

    def get_tools(self, **kwargs: Any) -> list[ToolSchema]:
        return self._registry.schemas()

    def execute_tool(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        args = arguments or {}
        return self._registry.execute(name, **args)

    async def execute_tool_async(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        args = arguments or {}
        return await self._registry.execute_async(name, **args)
