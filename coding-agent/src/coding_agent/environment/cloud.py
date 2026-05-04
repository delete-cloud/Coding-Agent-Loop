from __future__ import annotations

import json
import posixpath
import shlex
from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.environment import FileTools, WorkspaceSummary
from agentkit.tools import tool


@dataclass(frozen=True)
class CloudCommandResult:
    stdout: str
    stderr: str
    exit_code: int


class CloudWorkspaceClient(Protocol):
    workspace_id: str
    workspace_url: str
    default_cwd: str

    def read_file(self, path: str) -> str: ...

    def write_file(self, path: str, content: str) -> None: ...

    def replace_file(self, path: str, old: str, new: str) -> None: ...

    def glob_files(self, pattern: str, directory: str) -> list[str]: ...

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]: ...

    def apply_patch(self, path: str, patch: str) -> dict[str, Any]: ...

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult: ...


class CloudEnvironment:
    """Provider-neutral cloud workspace environment backed by a client."""

    def __init__(self, client: CloudWorkspaceClient) -> None:
        self._client: CloudWorkspaceClient = client

    @property
    def kind(self) -> str:
        return "cloud"

    def tool_config(self) -> dict[str, Any]:
        return {
            "workspace_id": self._client.workspace_id,
            "workspace_url": self._client.workspace_url,
        }

    def workspace_summary(self) -> WorkspaceSummary:
        return WorkspaceSummary(
            display_name=self._client.workspace_id,
            default_cwd=self._client.default_cwd,
        )

    def build_file_tools(self) -> FileTools:
        @tool(
            name="file_read",
            description="Read file contents. Returns file text.",
        )
        def file_read(path: str) -> str:
            return self._client.read_file(path)

        @tool(
            name="file_write",
            description="Write content to a file. Creates parent directories if needed.",
        )
        def file_write(path: str, content: str) -> str:
            self._client.write_file(path, content)
            return f"Written {len(content)} bytes to {path}"

        @tool(name="file_replace", description="Replace exact string in a file.")
        def file_replace(path: str, old: str, new: str) -> str:
            self._client.replace_file(path, old, new)
            return f"Replaced in {path}"

        @tool(name="glob_files", description="Search for files matching a glob pattern.")
        def glob_files(pattern: str, directory: str = ".") -> str:
            matches = self._client.glob_files(pattern, directory)
            if not matches:
                return "No files matched."
            return "\n".join(matches[:100])

        @tool(name="grep_search", description="Search file contents for a regex pattern.")
        def grep_search(pattern: str, directory: str = ".", include: str = "") -> str:
            matches = self._client.grep_search(pattern, directory, include)
            if not matches:
                return "No matches found."
            if len(matches) > 50:
                return "\n".join(matches[:50]) + f"\n... ({len(matches)} total matches)"
            return "\n".join(matches)

        return file_read, file_write, file_replace, glob_files, grep_search

    def build_file_patch_tool(self):
        @tool(
            name="file_patch",
            description="Apply a unified diff patch to an existing file. The patch should contain @@ hunk headers.",
        )
        def file_patch(path: str, patch: str) -> str:
            return json.dumps(self._client.apply_patch(path, patch))

        return file_patch

    def _resolve_cwd(self, cwd: str | None, target: str) -> str:
        base = cwd or self._client.default_cwd
        resolved = target if target.startswith("/") else posixpath.join(base, target)
        normalized = posixpath.normpath(resolved)
        workspace_root = posixpath.normpath(self._client.default_cwd)
        if normalized != workspace_root and not normalized.startswith(
            workspace_root.rstrip("/") + "/"
        ):
            raise ValueError(f"Directory is outside cloud workspace: {normalized}")
        return normalized

    def _validate_command_cwd(self, cwd: str | None) -> str | None:
        if cwd is None:
            return None
        try:
            return self._resolve_cwd(self._client.default_cwd, cwd)
        except ValueError as exc:
            message = str(exc).removeprefix("Directory is outside cloud workspace")
            raise ValueError(
                f"Working directory is outside cloud workspace{message}"
            ) from exc

    def _apply_cd(self, command: str, cwd: str | None) -> str | None:
        args = shlex.split(command)
        if not args or args[0] != "cd":
            return None
        if len(args) != 2:
            raise ValueError("cd requires exactly one target directory")
        return self._resolve_cwd(cwd, args[1])

    def _apply_export(
        self,
        command: str,
        env: dict[str, str] | None,
    ) -> tuple[str, str] | None:
        args = shlex.split(command)
        if not args or args[0] != "export":
            return None
        if len(args) != 2 or "=" not in args[1]:
            raise ValueError("export requires KEY=VALUE")
        key, value = args[1].split("=", 1)
        if not key:
            raise ValueError("export requires a non-empty variable name")
        if env is not None:
            env[key] = value
        return key, value

    def build_shell_tool(self):
        @tool(description="Run a shell command and return stdout/stderr.")
        def bash_run(
            command: str,
            timeout: int = 120,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
        ) -> str:
            try:
                changed_dir = self._apply_cd(command, cwd)
                if changed_dir is not None:
                    return f"Changed directory to {changed_dir}"
                exported = self._apply_export(command, env)
                if exported is not None:
                    key, value = exported
                    return f"Exported {key}={value}"
            except ValueError as exc:
                return f"Error: {exc}"

            try:
                execution_cwd = self._validate_command_cwd(cwd)
                result = self._client.run_command(
                    command,
                    cwd=execution_cwd,
                    env=env,
                    timeout=timeout,
                )
            except ValueError as exc:
                return f"Error: {exc}"
            except TimeoutError:
                return f"Error: command timed out after {timeout}s"
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.exit_code != 0:
                output += f"\nExit code: {result.exit_code}"
            return output.strip() or "(no output)"

        return bash_run
