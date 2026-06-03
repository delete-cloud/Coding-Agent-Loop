from __future__ import annotations

from dataclasses import dataclass

from coding_agent.approval import ApprovalPolicy
from coding_agent.runs import (
    CloudWorkspaceRef,
    ExternalWorkerExecutorRef,
    ExternalWorkerWorkspaceRef,
    IsolationPolicy,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
    RuntimeRunMetadataService,
    run_target_execution_placement,
)


@dataclass
class FakeSession:
    default_run_target: RunTarget
    id: str = "session-1"
    provider_name: str | None = "openai"
    model_name: str | None = "gpt-test"
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    max_steps: int = 12


@dataclass(frozen=True)
class FakeResumeContext:
    previous_run_id: str = "run-0"

    def metadata(self) -> dict[str, object]:
        return {
            "previous_run_id": self.previous_run_id,
            "resume_reason": "manual",
    }


def _local_target() -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path="/workspace"),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


def _cloud_target() -> RunTarget:
    return RunTarget(
        workspace=CloudWorkspaceRef(
            workspace_url="https://workspace.example",
            workspace_id="workspace-1",
        ),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(
            kind="provider_sandbox",
            network="provider_managed",
            filesystem="provider_managed",
            secrets="provider_managed",
        ),
    )


def _external_worker_target(workspace_ref: dict[str, object]) -> RunTarget:
    return RunTarget(
        workspace=ExternalWorkerWorkspaceRef(ref=workspace_ref),
        executor=ExternalWorkerExecutorRef(
            executor_kind="local_cli",
            worker_pool="pool-a",
        ),
        isolation=IsolationPolicy(kind="external_worker_policy"),
    )


def test_runtime_metadata_service_reports_local_run_target() -> None:
    service = RuntimeRunMetadataService()
    session = FakeSession(default_run_target=_local_target())

    metadata = service.metadata_for_session(
        session,
        resume_context=FakeResumeContext(),
    )

    assert metadata == {
        "provider_name": "openai",
        "model_name": "gpt-test",
        "approval_policy": "auto",
        "max_steps": 12,
        "executor_kind": "local_daemon",
        "workspace_surface": "local_workspace",
        "execution_plane": "control_plane",
        "execution_placement": "server_embedded",
        "previous_run_id": "run-0",
        "resume_reason": "manual",
    }


def test_runtime_metadata_service_reports_cloud_run_target() -> None:
    service = RuntimeRunMetadataService()
    target = _cloud_target()

    metadata = service.metadata_for_session(FakeSession(default_run_target=target))

    assert metadata["executor_kind"] == "local_daemon"
    assert metadata["workspace_surface"] == "cloud_workspace"
    assert metadata["execution_plane"] == "control_plane"
    assert metadata["execution_placement"] == "server_embedded"
    assert run_target_execution_placement(target) == "server_embedded"


def test_runtime_metadata_service_reports_external_worker_metadata() -> None:
    service = RuntimeRunMetadataService()
    workspace_ref = {"kind": "snapshot", "snapshot_id": "snap-1"}
    target = _external_worker_target(workspace_ref)

    metadata = service.metadata_for_session(FakeSession(default_run_target=target))

    assert metadata["executor_ref_kind"] == "external_worker"
    assert metadata["workspace_surface"] == "external_worker_workspace_ref"
    assert metadata["execution_plane"] == "executor_plane"
    assert metadata["execution_placement"] == "local_attached"
    assert metadata["executor_kind"] == "local_cli"
    assert metadata["worker_pool"] == "pool-a"
    assert metadata["workspace_ref"] == workspace_ref
    assert metadata["workspace_ref"] is not workspace_ref
    assert run_target_execution_placement(target) == "local_attached"
