from __future__ import annotations

import pytest

from coding_agent.environment.execution_binding import (
    CloudWorkspaceBinding,
    ExternalWorkerBinding,
    LocalAttachedExecutionBinding,
    LocalExecutionBinding,
)
from coding_agent.runs import (
    CloudWorkspaceRef,
    ExternalWorkerExecutorRef,
    ExternalWorkerWorkspaceRef,
    LocalAttachedExecutorRef,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    ManagedPoolExecutorRef,
    RunConstraints,
    RunTarget,
    IsolationPolicy,
    run_target_from_execution_binding,
)


def test_local_execution_binding_maps_to_local_daemon_run_target() -> None:
    binding = LocalExecutionBinding(
        workspace_root="/repo",
        workspace_provider="local",
        provider_instance_id="local-1",
    )

    target = run_target_from_execution_binding(binding)

    assert isinstance(target.workspace, LocalPathWorkspaceRef)
    assert target.workspace.path == "/repo"
    assert target.workspace.workspace_provider == "local"
    assert target.workspace.provider_instance_id == "local-1"
    assert isinstance(target.executor, LocalDaemonExecutorRef)
    assert target.executor.kind == "local_daemon"
    assert target.isolation.kind == "default_local_sandbox"
    assert target.isolation.filesystem == "workspace_scoped"


def test_cloud_execution_binding_maps_to_managed_pool_run_target() -> None:
    binding = CloudWorkspaceBinding(
        workspace_url="docker://workspace/ws-1",
        workspace_id="ws-1",
        runtime_profile="python",
        workspace_provider="docker",
        provider_instance_id="docker-1",
    )

    target = run_target_from_execution_binding(binding)

    assert isinstance(target.workspace, CloudWorkspaceRef)
    assert target.workspace.workspace_url == "docker://workspace/ws-1"
    assert target.workspace.workspace_id == "ws-1"
    assert target.workspace.runtime_profile == "python"
    assert target.workspace.workspace_provider == "docker"
    assert target.workspace.provider_instance_id == "docker-1"
    assert isinstance(target.executor, ManagedPoolExecutorRef)
    assert target.executor.pool == "default"
    assert target.isolation.kind == "provider_sandbox"
    assert target.isolation.network == "provider_managed"


def test_external_worker_binding_maps_to_external_worker_run_target() -> None:
    binding = ExternalWorkerBinding(
        executor_kind="local_cli",
        worker_pool="pool-a",
        workspace_ref={"path": "/repo", "label": "checkout"},
        provider_instance_id="worker-1",
    )

    target = run_target_from_execution_binding(binding)

    assert isinstance(target.workspace, ExternalWorkerWorkspaceRef)
    assert target.workspace.ref == {"path": "/repo", "label": "checkout"}
    assert target.workspace.provider_instance_id == "worker-1"
    assert isinstance(target.executor, ExternalWorkerExecutorRef)
    assert target.executor.executor_kind == "local_cli"
    assert target.executor.worker_pool == "pool-a"
    assert target.isolation.kind == "external_worker_policy"


def test_local_attached_binding_maps_to_local_attached_run_target() -> None:
    binding = LocalAttachedExecutionBinding(
        executor_kind="local_cli",
        worker_pool="attached",
        workspace_ref={"socket": "local-daemon.sock"},
    )

    target = run_target_from_execution_binding(binding)

    assert isinstance(target.workspace, ExternalWorkerWorkspaceRef)
    assert target.workspace.ref == {"socket": "local-daemon.sock"}
    assert isinstance(target.executor, LocalAttachedExecutorRef)
    assert target.executor.executor_kind == "local_cli"
    assert target.executor.worker_pool == "attached"
    assert target.isolation.kind == "external_worker_policy"


def test_run_target_rejects_empty_annotations_key() -> None:
    with pytest.raises(ValueError, match="annotation keys must be non-empty"):
        _ = RunTarget(
            workspace=LocalPathWorkspaceRef(path="/repo"),
            executor=LocalDaemonExecutorRef(),
            isolation=IsolationPolicy(kind="default_local_sandbox"),
            annotations={" ": "invalid"},
        )


def test_run_constraints_reject_invalid_limits() -> None:
    with pytest.raises(ValueError, match="max_steps must be positive"):
        _ = RunConstraints(max_steps=0)

    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        _ = RunConstraints(timeout_seconds=-1)

    with pytest.raises(ValueError, match="max_cost_usd must be non-negative"):
        _ = RunConstraints(max_cost_usd=-0.01)


def test_external_worker_workspace_ref_copies_input_metadata() -> None:
    source = {"path": "/repo"}

    ref = ExternalWorkerWorkspaceRef(ref=source)
    source["path"] = "/changed"

    assert ref.ref == {"path": "/repo"}
