from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.environment import SandboxedEnvironment
from coding_agent.runs import (
    CloudWorkspaceRef,
    InlineExecutorRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
    SnapshotWorkspaceRef,
)
from coding_agent.runs.environment import RuntimeEnvironmentResolverService


class FakeCloudClient:
    workspace_url = "https://workspace.example.test/w/1"
    workspace_id = "workspace-1"
    default_cwd = "/workspace"

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError

    def replace_file(self, path: str, old: str, new: str) -> None:
        raise NotImplementedError

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        raise NotImplementedError

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        raise NotImplementedError

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        raise NotImplementedError

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ):
        raise NotImplementedError


def test_runtime_environment_resolver_resolves_local_target(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    service = RuntimeEnvironmentResolverService()
    target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(workspace)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )

    environment = service.resolve_environment_for_run_target(target)

    assert isinstance(environment, SandboxedEnvironment)
    assert environment.workspace_root == workspace.resolve()
    assert environment.tool_config()["shell"] == {"sandbox_mode": "native"}
    assert environment.tool_config()["isolation_policy"] == target.isolation.to_dict()
    assert service.workspace_root_for_environment(environment) == workspace.resolve()


def test_runtime_environment_resolver_delegates_cloud_workspace() -> None:
    resolved_workspaces: list[CloudWorkspaceRef] = []

    def factory(workspace: CloudWorkspaceRef) -> FakeCloudClient:
        resolved_workspaces.append(workspace)
        return FakeCloudClient()

    service = RuntimeEnvironmentResolverService(factory)
    target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="https://workspace.example.test/w/1",
            workspace_id="workspace-1",
            runtime_profile="python",
            workspace_provider="provider-a",
            provider_instance_id="instance-1",
        ),
        executor=InlineExecutorRef(),
        isolation=IsolationPolicy(kind="provider_sandbox"),
    )

    environment = service.resolve_environment_for_run_target(target)

    assert environment.kind == "cloud"
    assert environment.tool_config() == {
        "workspace_id": "workspace-1",
        "workspace_url": "https://workspace.example.test/w/1",
    }
    assert resolved_workspaces == [
        CloudWorkspaceRef(
            workspace_url="https://workspace.example.test/w/1",
            workspace_id="workspace-1",
            runtime_profile="python",
            workspace_provider="provider-a",
            provider_instance_id="instance-1",
        )
    ]
    assert service.workspace_root_for_environment(environment) is None


def test_runtime_environment_resolver_rejects_missing_target() -> None:
    service = RuntimeEnvironmentResolverService()

    with pytest.raises(RuntimeError, match="session is missing default_run_target"):
        service.resolve_environment_for_run_target(None)


def test_runtime_environment_resolver_rejects_unsupported_workspace() -> None:
    service = RuntimeEnvironmentResolverService()
    target = RunTarget(
        workspace=SnapshotWorkspaceRef(snapshot_id="snapshot-1"),
        executor=InlineExecutorRef(),
        isolation=IsolationPolicy(kind="provider_sandbox"),
    )

    with pytest.raises(
        ValueError,
        match="runtime builders cannot resolve workspace target: snapshot",
    ):
        service.resolve_environment_for_run_target(target)


def test_runtime_environment_resolver_normalizes_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    service = RuntimeEnvironmentResolverService()
    target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(workspace / ".")),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )

    environment = service.resolve_environment_for_run_target(target)

    assert service.workspace_root_for_environment(environment) == workspace.resolve()
