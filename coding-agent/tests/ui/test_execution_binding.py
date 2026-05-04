from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.ui.binding_resolver import (
    CloudBindingNotImplementedError,
    DefaultBindingResolver,
)
from coding_agent.environment import CloudCommandResult, CloudEnvironment, LocalEnvironment
from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)


class FakeCloudClient:
    workspace_url = "https://workspace.example.com"
    workspace_id = "ws-123"
    default_cwd = "/workspace"

    def read_file(self, path: str) -> str:
        return f"read:{path}"

    def write_file(self, path: str, content: str) -> None:
        del path, content

    def replace_file(self, path: str, old: str, new: str) -> None:
        del path, old, new

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return []

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del pattern, directory, include
        return []

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        del patch
        return {"success": True, "path": path, "changed": False}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        del command, cwd, env, timeout
        return CloudCommandResult(stdout="", stderr="", exit_code=0)


def test_local_binding_round_trip(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, LocalExecutionBinding)
    assert restored.workspace_root == str(workspace)


def test_cloud_binding_round_trip() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )

    restored = ExecutionBinding.from_dict(binding.to_dict())

    assert isinstance(restored, CloudWorkspaceBinding)
    assert restored.workspace_url == "https://workspace.example.com"
    assert restored.workspace_id == "ws-123"


def test_unknown_binding_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown binding kind"):
        ExecutionBinding.from_dict({"kind": "unknown"})


def test_local_binding_requires_string_workspace_root() -> None:
    with pytest.raises(TypeError, match="string workspace_root"):
        LocalExecutionBinding.from_dict({"kind": "local", "workspace_root": 123})


def test_local_resolver_returns_absolute_path(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))
    resolver = DefaultBindingResolver()

    resolved = resolver.resolve_workspace_root(binding)
    assert resolved == workspace.resolve()
    assert resolver.resolve_tool_config(binding) == {"workspace_root": str(resolved)}


def test_local_resolver_returns_local_environment(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    binding = LocalExecutionBinding(workspace_root=str(workspace))
    resolver = DefaultBindingResolver()

    env = resolver.resolve_environment(binding)

    assert isinstance(env, LocalEnvironment)
    assert env.workspace_root == workspace.resolve()


def test_cloud_resolver_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        resolver.resolve_workspace_root(binding)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"kind": "cloud", "workspace_url": 123, "workspace_id": "ws-123"},
            "string workspace_url and workspace_id",
        ),
        (
            {
                "kind": "cloud",
                "workspace_url": "https://workspace.example.com",
                "workspace_id": 123,
            },
            "string workspace_url and workspace_id",
        ),
    ],
)
def test_cloud_binding_rejects_invalid_field_types(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        CloudWorkspaceBinding.from_dict(payload)


def test_cloud_resolver_tool_config_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        resolver.resolve_tool_config(binding)


def test_cloud_resolver_environment_raises_typed_not_implemented() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    resolver = DefaultBindingResolver()

    with pytest.raises(CloudBindingNotImplementedError, match="cloud workspace"):
        resolver.resolve_environment(binding)


def test_cloud_binding_resolves_to_cloud_environment_when_client_available() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example.com",
        workspace_id="ws-123",
    )
    captured: list[CloudWorkspaceBinding] = []

    def build_client(resolved_binding: CloudWorkspaceBinding) -> FakeCloudClient:
        captured.append(resolved_binding)
        return FakeCloudClient()

    resolver = DefaultBindingResolver(cloud_client_factory=build_client)

    env = resolver.resolve_environment(binding)

    assert isinstance(env, CloudEnvironment)
    assert captured == [binding]
    assert env.tool_config() == {
        "workspace_id": "ws-123",
        "workspace_url": "https://workspace.example.com",
    }
