from __future__ import annotations

from typing import Any, Callable

from agentkit.tools import ToolRegistry, ToolSchema


class CoreToolsPlugin:
    state_key = "core_tools"

    def __init__(self) -> None:
        self._registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        from coding_agent.tools.file_ops import (
            file_read,
            file_replace,
            file_write,
            glob_files,
            grep_search,
        )
        from coding_agent.tools.planner import todo_read, todo_write
        from coding_agent.tools.shell import bash_run

        for fn in (
            file_read,
            file_write,
            file_replace,
            glob_files,
            grep_search,
            bash_run,
            todo_write,
            todo_read,
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
