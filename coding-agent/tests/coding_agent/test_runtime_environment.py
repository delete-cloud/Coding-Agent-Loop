from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentkit.environment import FileTools, WorkspaceSummary
from coding_agent.environment import SandboxedEnvironment
from coding_agent.environment.execution_binding import CloudWorkspaceBinding
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


class FakeCloudEnvironment:
    kind = "fake_cloud"

    def __init__(self, local_root: str | None = None) -> None:
        self._local_root = local_root

    def workspace_summary(self) -> WorkspaceSummary:
        return WorkspaceSummary(
            display_name="cloud",
            default_cwd=None,
            local_root=self._local_root,
        )

    def tool_config(self) -> dict[str, Any]:
        return {"shell": {"sandbox_mode": "provider"}}

    def build_file_tools(self) -> FileTools:
        raise NotImplementedError

    def build_file_patch_tool(self):
        raise NotImplementedError

    def build_shell_tool(self):
        raise NotImplementedError


class FakeBindingResolver:
    def __init__(self, environment: FakeCloudEnvironment | None = None) -> None:
        self.environment = environment or FakeCloudEnvironment()
        self.bindings: list[CloudWorkspaceBinding] = []

    def resolve_environment(self, binding: CloudWorkspaceBinding):
        self.bindings.append(binding)
        return self.environment


def test_runtime_environment_resolver_resolves_local_target(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    service = RuntimeEnvironmentResolverService(FakeBindingResolver())
    target = RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(workspace)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )

    environment = service.resolve_environment_for_run_target(target)

    assert isinstance(environment, SandboxedEnvironment)
    assert environment.workspace_root == workspace.resolve()
    assert environment.tool_config()["shell"] == {"sandbox_mode": "none"}
    assert environment.tool_config()["isolation_policy"] == target.isolation.to_dict()
    assert service.workspace_root_for_environment(environment) == workspace.resolve()


def test_runtime_environment_resolver_delegates_cloud_workspace() -> None:
    cloud_environment = FakeCloudEnvironment()
    binding_resolver = FakeBindingResolver(cloud_environment)
    service = RuntimeEnvironmentResolverService(binding_resolver)
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

    assert environment is cloud_environment
    assert binding_resolver.bindings == [
        CloudWorkspaceBinding(
            workspace_url="https://workspace.example.test/w/1",
            workspace_id="workspace-1",
            runtime_profile="python",
            workspace_provider="provider-a",
            provider_instance_id="instance-1",
        )
    ]
    assert service.workspace_root_for_environment(environment) is None


def test_runtime_environment_resolver_rejects_missing_target() -> None:
    service = RuntimeEnvironmentResolverService(FakeBindingResolver())

    with pytest.raises(RuntimeError, match="session is missing default_run_target"):
        service.resolve_environment_for_run_target(None)


def test_runtime_environment_resolver_rejects_unsupported_workspace() -> None:
    service = RuntimeEnvironmentResolverService(FakeBindingResolver())
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
    service = RuntimeEnvironmentResolverService(
        FakeBindingResolver(FakeCloudEnvironment(str(workspace)))
    )
    target = RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="https://workspace.example.test/w/1",
            workspace_id="workspace-1",
        ),
        executor=InlineExecutorRef(),
        isolation=IsolationPolicy(kind="provider_sandbox"),
    )

    environment = service.resolve_environment_for_run_target(target)

    assert service.workspace_root_for_environment(environment) == workspace.resolve()
