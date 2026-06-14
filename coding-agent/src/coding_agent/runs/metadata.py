from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from coding_agent.stores.runtime_store import JSONObject, JSONValue

from .lifecycle import RuntimeRunResumeContext
from .target import (
    ExternalWorkerWorkspaceRef,
    RunTarget,
    run_target_execution_placement,
    run_target_execution_plane,
    run_target_executor_kind,
    run_target_executor_ref_kind,
    run_target_worker_pool,
    run_target_workspace_surface,
)


class RuntimeApprovalPolicy(Protocol):
    value: str


class RuntimeMetadataSession(Protocol):
    id: str
    provider_name: str | None
    model_name: str | None
    approval_policy: RuntimeApprovalPolicy
    max_steps: int
    default_run_target: RunTarget


@dataclass(frozen=True)
class RuntimeRunMetadataService:
    def metadata_for_session(
        self,
        session: RuntimeMetadataSession,
        *,
        resume_context: RuntimeRunResumeContext | None = None,
    ) -> JSONObject:
        target = session.default_run_target
        metadata: JSONObject = {
            "provider_name": session.provider_name,
            "model_name": session.model_name,
            "approval_policy": session.approval_policy.value,
            "max_steps": session.max_steps,
            "executor_kind": run_target_executor_kind(target),
            "workspace_surface": run_target_workspace_surface(target),
            "execution_plane": run_target_execution_plane(target),
            "execution_placement": run_target_execution_placement(target),
        }
        if resume_context is not None:
            metadata.update(resume_context.metadata())
        worker_pool = run_target_worker_pool(target)
        if worker_pool is not None:
            metadata["worker_pool"] = worker_pool
        executor_ref_kind = run_target_executor_ref_kind(target)
        if executor_ref_kind is not None:
            metadata["executor_ref_kind"] = executor_ref_kind
            if isinstance(target.workspace, ExternalWorkerWorkspaceRef):
                metadata["workspace_ref"] = cast(JSONValue, dict(target.workspace.ref))
        return metadata


__all__ = [
    "RuntimeMetadataSession",
    "RuntimeRunMetadataService",
]
