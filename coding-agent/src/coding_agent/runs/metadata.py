from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from coding_agent.environment.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    ExternalWorkerBinding,
)
from coding_agent.runtime_store import JSONObject, JSONValue

from .lifecycle import RuntimeRunResumeContext


class RuntimeApprovalPolicy(Protocol):
    value: str


class RuntimeMetadataSession(Protocol):
    id: str
    provider_name: str | None
    model_name: str | None
    approval_policy: RuntimeApprovalPolicy
    max_steps: int
    execution_binding: ExecutionBinding


def runtime_execution_placement(binding: ExecutionBinding) -> str:
    if isinstance(binding, ExternalWorkerBinding):
        return "local_attached"
    if isinstance(binding, CloudWorkspaceBinding):
        return "cloud_workspace"
    return "server_embedded"


@dataclass(frozen=True)
class RuntimeRunMetadataService:
    def metadata_for_session(
        self,
        session: RuntimeMetadataSession,
        *,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> JSONObject:
        binding = session.execution_binding
        metadata: JSONObject = {
            "provider_name": session.provider_name,
            "model_name": session.model_name,
            "approval_policy": session.approval_policy.value,
            "max_steps": session.max_steps,
            "execution_binding_kind": binding.kind,
            "workspace_surface": binding.workspace_surface,
            "execution_plane": binding.execution_plane,
            "execution_placement": runtime_execution_placement(binding),
        }
        if resume_context is not None:
            metadata.update(resume_context.metadata())
        if isinstance(binding, ExternalWorkerBinding):
            metadata["executor_kind"] = binding.executor_kind
            metadata["worker_pool"] = binding.worker_pool
            if binding.workspace_ref is not None:
                metadata["workspace_ref"] = cast(JSONValue, dict(binding.workspace_ref))
        return metadata


__all__ = [
    "RuntimeMetadataSession",
    "RuntimeRunMetadataService",
    "runtime_execution_placement",
]
