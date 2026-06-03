from __future__ import annotations

import pytest

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
    RunTargetSerializationError,
    IsolationPolicy,
    run_target_from_dict,
    run_target_from_legacy_session_payload,
)


def test_legacy_local_session_payload_maps_to_local_daemon_run_target() -> None:
    target = run_target_from_legacy_session_payload(
        {
            "kind": "local",
            "workspace_root": "/repo",
            "workspace_provider": "local",
            "provider_instance_id": "local-1",
        }
    )

    assert isinstance(target.workspace, LocalPathWorkspaceRef)
    assert target.workspace.path == "/repo"
    assert target.workspace.workspace_provider == "local"
    assert target.workspace.provider_instance_id == "local-1"
    assert isinstance(target.executor, LocalDaemonExecutorRef)
    assert target.executor.kind == "local_daemon"
    assert target.isolation.kind == "default_local_sandbox"
    assert target.isolation.filesystem == "workspace_scoped"


def test_run_target_round_trips_local_daemon_metadata() -> None:
    target = RunTarget(
        workspace=LocalPathWorkspaceRef(
            path="/repo",
            workspace_provider="local",
            provider_instance_id="local-1",
        ),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
        constraints=RunConstraints(max_steps=12, timeout_seconds=300),
        annotations={"origin": "test"},
    )

    payload = target.to_dict()
    reloaded = run_target_from_dict(payload)

    assert reloaded == target
    assert payload["workspace"] == {
        "kind": "local_path",
        "path": "/repo",
        "workspace_provider": "local",
        "provider_instance_id": "local-1",
    }
    assert payload["executor"] == {"kind": "local_daemon"}


def test_legacy_cloud_session_payload_maps_to_managed_pool_run_target() -> None:
    target = run_target_from_legacy_session_payload(
        {
            "kind": "cloud",
            "workspace_url": "docker://workspace/ws-1",
            "workspace_id": "ws-1",
            "runtime_profile": "python",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-1",
        }
    )

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


def test_run_target_round_trips_cloud_metadata() -> None:
    target = run_target_from_legacy_session_payload(
        {
            "kind": "cloud",
            "workspace_url": "docker://workspace/ws-1",
            "workspace_id": "ws-1",
            "runtime_profile": "python",
            "workspace_provider": "docker",
            "provider_instance_id": "docker-1",
        }
    )

    assert run_target_from_dict(target.to_dict()) == target


def test_legacy_external_worker_payload_maps_to_external_worker_run_target() -> None:
    target = run_target_from_legacy_session_payload(
        {
            "kind": "external_worker",
            "executor_kind": "local_cli",
            "worker_pool": "pool-a",
            "workspace_ref": {"path": "/repo", "label": "checkout"},
            "provider_instance_id": "worker-1",
        }
    )

    assert isinstance(target.workspace, ExternalWorkerWorkspaceRef)
    assert target.workspace.ref == {"path": "/repo", "label": "checkout"}
    assert target.workspace.provider_instance_id == "worker-1"
    assert isinstance(target.executor, ExternalWorkerExecutorRef)
    assert target.executor.executor_kind == "local_cli"
    assert target.executor.worker_pool == "pool-a"
    assert target.isolation.kind == "external_worker_policy"


def test_run_target_round_trips_external_worker_metadata() -> None:
    target = run_target_from_legacy_session_payload(
        {
            "kind": "external_worker",
            "executor_kind": "local_cli",
            "worker_pool": "pool-a",
            "workspace_ref": {"path": "/repo", "label": "checkout"},
            "provider_instance_id": "worker-1",
        }
    )

    assert run_target_from_dict(target.to_dict()) == target


def test_legacy_local_attached_payload_maps_to_local_attached_run_target() -> None:
    target = run_target_from_legacy_session_payload(
        {
            "kind": "local_attached",
            "executor_kind": "local_cli",
            "worker_pool": "attached",
            "workspace_ref": {"socket": "local-daemon.sock"},
        }
    )

    assert isinstance(target.workspace, ExternalWorkerWorkspaceRef)
    assert target.workspace.ref == {"socket": "local-daemon.sock"}
    assert isinstance(target.executor, LocalAttachedExecutorRef)
    assert target.executor.executor_kind == "local_cli"
    assert target.executor.worker_pool == "attached"
    assert target.isolation.kind == "external_worker_policy"


def test_run_target_round_trips_local_attached_metadata() -> None:
    target = run_target_from_legacy_session_payload(
        {
            "kind": "local_attached",
            "executor_kind": "local_cli",
            "worker_pool": "attached",
            "workspace_ref": {"socket": "local-daemon.sock"},
        }
    )

    assert run_target_from_dict(target.to_dict()) == target


def test_run_target_from_dict_rejects_unknown_workspace_kind() -> None:
    with pytest.raises(
        RunTargetSerializationError,
        match="unknown workspace ref kind",
    ):
        run_target_from_dict(
            {
                "workspace": {"kind": "mystery"},
                "executor": {"kind": "local_daemon"},
                "isolation": {"kind": "default_local_sandbox"},
            }
        )


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
