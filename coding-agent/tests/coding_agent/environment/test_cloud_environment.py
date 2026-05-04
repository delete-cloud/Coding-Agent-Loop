from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tools import ToolCallRequest, Toolset
from coding_agent.environment import CloudCommandResult, CloudEnvironment
from coding_agent.plugins.core_tools import CoreToolsPlugin
from coding_agent.plugins.shell_session import ShellSessionPlugin


class FakeCloudWorkspaceClient:
    workspace_id: str = "ws-123"
    workspace_url: str = "https://workspace.example.com"
    default_cwd: str = "/workspace"

    def __init__(self) -> None:
        self.files: dict[str, str] = {"note.txt": "hello cloud"}
        self.shell_calls: list[dict[str, Any]] = []
        self.fail_read: bool = False
        self.command_timeout: bool = False

    def read_file(self, path: str) -> str:
        if self.fail_read:
            raise RuntimeError("cloud read failed")
        return self.files[path]

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def replace_file(self, path: str, old: str, new: str) -> None:
        self.files[path] = self.files[path].replace(old, new, 1)

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return sorted(self.files)

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del directory, include
        return [
            f"{path}:1:{content}"
            for path, content in sorted(self.files.items())
            if pattern in content
        ]

    def apply_patch(self, path: str, patch: str) -> dict[str, Any]:
        self.files[path] = self.files[path] + patch
        return {"success": True, "path": path, "changed": True}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        if self.command_timeout:
            raise TimeoutError("cloud command timed out")
        self.shell_calls.append(
            {"command": command, "cwd": cwd, "env": env, "timeout": timeout}
        )
        return CloudCommandResult(stdout="cloud output\n", stderr="", exit_code=0)


def test_cloud_environment_file_tools_use_client_without_local_filesystem() -> None:
    client = FakeCloudWorkspaceClient()
    env = CloudEnvironment(client)
    file_read, file_write, file_replace, glob_files, grep_search = env.build_file_tools()
    file_patch = env.build_file_patch_tool()

    assert env.kind == "cloud"
    assert env.tool_config() == {
        "workspace_id": "ws-123",
        "workspace_url": "https://workspace.example.com",
    }
    assert env.workspace_summary().display_name == "ws-123"
    assert env.workspace_summary().default_cwd == "/workspace"
    assert env.workspace_summary().local_root is None

    assert file_read("note.txt") == "hello cloud"
    assert file_write("created.txt", "new cloud file") == (
        "Written 14 bytes to created.txt"
    )
    assert client.files["created.txt"] == "new cloud file"
    assert file_replace("created.txt", "new", "updated") == "Replaced in created.txt"
    assert client.files["created.txt"] == "updated cloud file"
    assert glob_files("*.txt", ".") == "created.txt\nnote.txt"
    assert grep_search("hello", ".", "*.txt") == "note.txt:1:hello cloud"
    assert json.loads(file_patch("note.txt", "+ patched")) == {
        "success": True,
        "path": "note.txt",
        "changed": True,
    }
    assert "+ patched" in client.files["note.txt"]


def test_cloud_environment_shell_tool_preserves_cwd_and_env() -> None:
    client = FakeCloudWorkspaceClient()
    shell_tool = CloudEnvironment(client).build_shell_tool()

    result = shell_tool(
        "pytest -q",
        cwd="/workspace/pkg",
        env={"PYTHONPATH": "src"},
        timeout=7,
    )

    assert result == "cloud output"
    assert client.shell_calls == [
        {
            "command": "pytest -q",
            "cwd": "/workspace/pkg",
            "env": {"PYTHONPATH": "src"},
            "timeout": 7,
        }
    ]


def test_cloud_environment_shell_tool_updates_session_cwd_and_env() -> None:
    client = FakeCloudWorkspaceClient()
    shell_session = ShellSessionPlugin()
    _ = shell_session.do_mount(
        ctx=type("Ctx", (), {"config": {"environment": CloudEnvironment(client)}})()
    )
    plugin = CoreToolsPlugin(
        environment=CloudEnvironment(client),
        shell_session=shell_session,
    )

    cd_result = cast(
        str,
        plugin.execute_tool(
            name="bash_run",
            arguments={"command": "cd pkg"},
        ),
    )
    export_result = cast(
        str,
        plugin.execute_tool(
            name="bash_run",
            arguments={"command": 'export TEST_VALUE="cloud ok"'},
        ),
    )
    command_result = cast(
        str,
        plugin.execute_tool(
            name="bash_run",
            arguments={"command": "pytest -q", "timeout": 7},
        ),
    )

    assert cd_result == "Changed directory to /workspace/pkg"
    assert export_result == "Exported TEST_VALUE=cloud ok"
    assert command_result == "cloud output"
    assert shell_session.get_session_context() == {
        "cwd": "/workspace/pkg",
        "env_vars": {"TEST_VALUE": "cloud ok"},
        "active": True,
    }
    assert client.shell_calls == [
        {
            "command": "pytest -q",
            "cwd": "/workspace/pkg",
            "env": {"TEST_VALUE": "cloud ok"},
            "timeout": 7,
        }
    ]


def test_cloud_environment_rejects_invalid_session_commands_without_mutation() -> None:
    client = FakeCloudWorkspaceClient()
    shell_session = ShellSessionPlugin()
    _ = shell_session.do_mount(
        ctx=type("Ctx", (), {"config": {"environment": CloudEnvironment(client)}})()
    )
    plugin = CoreToolsPlugin(
        environment=CloudEnvironment(client),
        shell_session=shell_session,
    )

    cd_result = cast(
        str,
        plugin.execute_tool(
            name="bash_run",
            arguments={"command": "cd one two"},
        ),
    )
    export_result = cast(
        str,
        plugin.execute_tool(
            name="bash_run",
            arguments={"command": "export MISSING_VALUE"},
        ),
    )

    assert cd_result == "Error: cd requires exactly one target directory"
    assert export_result == "Error: export requires KEY=VALUE"
    assert shell_session.get_session_context() == {
        "cwd": "/workspace",
        "env_vars": {},
        "active": True,
    }
    assert client.shell_calls == []


def test_cloud_environment_rejects_explicit_cwd_outside_workspace() -> None:
    client = FakeCloudWorkspaceClient()
    shell_tool = CloudEnvironment(client).build_shell_tool()

    absolute_result = shell_tool("pwd", cwd="/etc")
    relative_result = shell_tool("pwd", cwd="/workspace/../etc")

    assert absolute_result == "Error: Working directory is outside cloud workspace: /etc"
    assert relative_result == "Error: Working directory is outside cloud workspace: /etc"
    assert client.shell_calls == []


def test_cloud_environment_shell_timeout_is_model_visible() -> None:
    client = FakeCloudWorkspaceClient()
    client.command_timeout = True
    shell_tool = CloudEnvironment(client).build_shell_tool()

    result = shell_tool("sleep 10", timeout=7, cwd="/workspace", env={})

    assert result == "Error: command timed out after 7s"


@pytest.mark.asyncio
async def test_cloud_environment_tool_errors_do_not_fallback_to_local_execution(
    tmp_path: Path,
) -> None:
    local_note = tmp_path / "note.txt"
    local_note.write_text("local content")
    client = FakeCloudWorkspaceClient()
    client.fail_read = True
    plugin = CoreToolsPlugin(environment=CloudEnvironment(client))
    registry = PluginRegistry()
    registry.register(plugin)
    toolset = Toolset(runtime=HookRuntime(registry))

    results = await toolset.execute_tools(
        [
            ToolCallRequest(
                tool_call_id="call-1",
                name="file_read",
                arguments={"path": str(local_note)},
            )
        ],
        ctx=None,
    )

    assert len(results) == 1
    assert results[0].is_error is True
    assert isinstance(results[0].error, RuntimeError)
    assert "cloud read failed" in results[0].error_message
