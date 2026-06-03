from __future__ import annotations

from dataclasses import dataclass

from coding_agent.approval import ApprovalPolicy
from coding_agent.environment.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    ExternalWorkerBinding,
    LocalExecutionBinding,
)
from coding_agent.runs import RuntimeRunMetadataService, runtime_execution_placement


@dataclass
class FakeSession:
    execution_binding: ExecutionBinding
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


def test_runtime_metadata_service_reports_local_execution_binding() -> None:
    service = RuntimeRunMetadataService()
    session = FakeSession(
        execution_binding=LocalExecutionBinding(workspace_root="/workspace")
    )

    metadata = service.metadata_for_session(
        session,
        resume_context=FakeResumeContext(),
    )

    assert metadata == {
        "provider_name": "openai",
        "model_name": "gpt-test",
        "approval_policy": "auto",
        "max_steps": 12,
        "execution_binding_kind": "local",
        "workspace_surface": "local_workspace",
        "execution_plane": "control_plane",
        "execution_placement": "server_embedded",
        "previous_run_id": "run-0",
        "resume_reason": "manual",
    }


def test_runtime_metadata_service_reports_cloud_execution_binding() -> None:
    service = RuntimeRunMetadataService()
    binding = CloudWorkspaceBinding(
        workspace_url="https://workspace.example",
        workspace_id="workspace-1",
    )

    metadata = service.metadata_for_session(FakeSession(execution_binding=binding))

    assert metadata["execution_binding_kind"] == "cloud"
    assert metadata["workspace_surface"] == "cloud_workspace"
    assert metadata["execution_plane"] == "control_plane"
    assert metadata["execution_placement"] == "cloud_workspace"
    assert runtime_execution_placement(binding) == "cloud_workspace"


def test_runtime_metadata_service_reports_external_worker_metadata() -> None:
    service = RuntimeRunMetadataService()
    workspace_ref = {"kind": "snapshot", "snapshot_id": "snap-1"}
    binding = ExternalWorkerBinding(
        executor_kind="local_cli",
        worker_pool="pool-a",
        workspace_ref=workspace_ref,
    )

    metadata = service.metadata_for_session(FakeSession(execution_binding=binding))

    assert metadata["execution_binding_kind"] == "external_worker"
    assert metadata["workspace_surface"] == "external_worker_workspace_ref"
    assert metadata["execution_plane"] == "executor_plane"
    assert metadata["execution_placement"] == "local_attached"
    assert metadata["executor_kind"] == "local_cli"
    assert metadata["worker_pool"] == "pool-a"
    assert metadata["workspace_ref"] == workspace_ref
    assert metadata["workspace_ref"] is not workspace_ref
    assert runtime_execution_placement(binding) == "local_attached"
