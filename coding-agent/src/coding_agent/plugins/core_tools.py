from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agentkit.tools import ToolRegistry, ToolSchema

from coding_agent.plugins.shell_session import ShellSessionPlugin


class CoreToolsPlugin:
    state_key = "core_tools"

    def __init__(
        self,
        workspace_root: Path | str = ".",
        planner: Any = None,
        shell_session: ShellSessionPlugin | None = None,
        web_search_backend: Any = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._planner = planner
        self._shell_session = shell_session
        self._web_search_backend = web_search_backend
        self._registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        from coding_agent.tools.file_ops import build_file_tools
        from coding_agent.tools.file_patch_tool import build_file_patch_tool
        from coding_agent.tools.planner import build_planner_tools
        from coding_agent.tools.shell import bash_run
        from coding_agent.tools.web_search import build_web_search_tool

        file_read, file_write, file_replace, glob_files, grep_search = build_file_tools(
            self._workspace_root
        )
        file_patch = build_file_patch_tool(self._workspace_root)
        todo_write, todo_read = build_planner_tools(self._planner)
        web_search = build_web_search_tool(self._web_search_backend)

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
            web_search,
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
        args = self._prepare_arguments(name, arguments)
        # Remove 'name' from args to avoid conflict with positional arg
        args.pop("name", None)
        result = self._registry.execute(name, **args)
        self._sync_shell_session(name, args, result)
        return result

    async def execute_tool_async(
        self, name: str = "", arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        args = self._prepare_arguments(name, arguments)
        # Remove 'name' from args to avoid conflict with positional arg
        args.pop("name", None)
        result = await self._registry.execute_async(name, **args)
        self._sync_shell_session(name, args, result)
        return result

    def _prepare_arguments(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        if name == "bash_run" and self._shell_session is not None:
            session = self._shell_session.get_session_context()
            args.setdefault("cwd", session.get("cwd"))
            args.setdefault("env", session.get("env_vars", {}))
        return args

    def _sync_shell_session(self, name: str, args: dict[str, Any], result: Any) -> None:
        if name != "bash_run" or self._shell_session is None:
            return

        result_text = str(result)
        if result_text.startswith("Changed directory to "):
            new_cwd = result_text.removeprefix("Changed directory to ").strip()
            if new_cwd:
                self._shell_session.update_cwd(new_cwd)
            return

        if result_text.startswith("Exported "):
            exported = result_text.removeprefix("Exported ").strip()
            if "=" in exported:
                key, value = exported.split("=", 1)
                if key:
                    self._shell_session.update_env(key, value)
