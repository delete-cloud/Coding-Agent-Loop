from __future__ import annotations

import json
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
        self._client = client

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

    def build_shell_tool(self):
        @tool(description="Run a shell command and return stdout/stderr.")
        def bash_run(
            command: str,
            timeout: int = 120,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
        ) -> str:
            result = self._client.run_command(
                command,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.exit_code != 0:
                output += f"\nExit code: {result.exit_code}"
            return output.strip() or "(no output)"

        return bash_run
