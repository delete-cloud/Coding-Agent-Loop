from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coding_agent.environment import CloudCommandResult, CloudEnvironment
from coding_agent.plugins.core_tools import CoreToolExecutor


@dataclass
class RecordingCloudClient:
    workspace_url: str = "https://workspace.example.com"
    workspace_id: str = "ws-selected"
    default_cwd: str = "/workspace"
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def read_file(self, path: str) -> str:
        self.calls.append(("file_read", {"path": path}))
        return "read-from-selected-client"

    def write_file(self, path: str, content: str) -> None:
        self.calls.append(("file_write", {"path": path, "content_size": len(content)}))

    def replace_file(self, path: str, old: str, new: str) -> None:
        self.calls.append(
            ("file_replace", {"path": path, "old_size": len(old), "new_size": len(new)})
        )

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        self.calls.append(("glob_files", {"pattern": pattern, "directory": directory}))
        return ["src/app.py"]

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        self.calls.append(
            (
                "grep_search",
                {"pattern": pattern, "directory": directory, "include": include},
            )
        )
        return ["src/app.py:1:TODO"]

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        self.calls.append(("file_patch", {"path": path, "patch_size": len(patch)}))
        return {"success": True, "changed": True}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        self.calls.append(
            (
                "bash_run",
                {
                    "command_label": command.split()[0],
                    "timeout": timeout,
                    "cwd": cwd,
                    "env_keys": tuple(sorted((env or {}).keys())),
                },
            )
        )
        return CloudCommandResult(
            stdout="ran-in-selected-client", stderr="", exit_code=0
        )


def test_action_tools_route_from_selected_binding_to_workspace_client() -> None:
    selected_client = RecordingCloudClient()

    environment = CloudEnvironment(selected_client)
    executor = CoreToolExecutor(environment=environment)

    calls: list[tuple[str, dict[str, Any]]] = [
        ("file_read", {"path": "src/app.py"}),
        ("file_write", {"path": "notes.txt", "content": "hello"}),
        ("file_replace", {"path": "notes.txt", "old": "hello", "new": "hi"}),
        ("glob_files", {"pattern": "*.py", "directory": "src"}),
        ("grep_search", {"pattern": "TODO", "directory": "src", "include": "*.py"}),
        ("file_patch", {"path": "notes.txt", "patch": "@@ -1 +1\n-hi\n+hello"}),
        ("bash_run", {"command": "pytest", "timeout": 5}),
    ]

    for name, arguments in calls:
        _ = executor.execute_tool(name=name, arguments=arguments)

    assert [name for name, _ in selected_client.calls] == [name for name, _ in calls]
    assert selected_client.calls[-1] == (
        "bash_run",
        {
            "command_label": "pytest",
            "timeout": 5,
            "cwd": "/workspace",
            "env_keys": (),
        },
    )
