"""Pydantic schemas for HTTP API request/response validation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from coding_agent.core.config import ProviderName


class LocalExecutionBindingRequest(BaseModel):
    kind: Literal["local"]
    workspace_root: str = Field(..., min_length=1, max_length=500)


class CloudWorkspaceBindingRequest(BaseModel):
    kind: Literal["cloud"]
    workspace_url: str = Field(..., min_length=1, max_length=500)
    workspace_id: str = Field(..., min_length=1, max_length=200)


class DockerWorkspaceSourceRequest(BaseModel):
    kind: Literal["docker"]
    snapshot_archive_base64: str | None = Field(None, min_length=1)
    runtime_profile: str | None = Field(None, min_length=1, max_length=100)
    setup_commands: (
        list[Annotated[str, Field(min_length=1, max_length=1000)]] | None
    ) = Field(None, min_length=1, max_length=20)


class GitWorkspaceSourceRequest(BaseModel):
    kind: Literal["git"]
    remote_url: str = Field(..., min_length=1, max_length=1000)
    base_ref: str = Field(..., min_length=1, max_length=200)
    base_sha: str = Field(..., min_length=1, max_length=100)
    runtime_profile: str | None = Field(None, min_length=1, max_length=100)


ExecutionBindingRequest = LocalExecutionBindingRequest | CloudWorkspaceBindingRequest
WorkspaceSourceRequest = DockerWorkspaceSourceRequest | GitWorkspaceSourceRequest


class PromptRequest(BaseModel):
    """Request schema for sending a prompt."""

    prompt: str = Field(..., min_length=1, max_length=10000)


class CreateSessionRequest(BaseModel):
    """Request schema for creating a session."""

    repo_path: str | None = Field(None, max_length=500)
    execution_binding: ExecutionBindingRequest | None = None
    workspace_source: WorkspaceSourceRequest | None = None
    approval_policy: str = Field("auto", pattern="^(yolo|interactive|auto)$")
    provider: ProviderName | None = None
    model: str | None = Field(None, min_length=1, max_length=200)
    base_url: str | None = Field(None, min_length=1, max_length=500)
    max_steps: int | None = Field(None, ge=0)


class ApproveRequest(BaseModel):
    """Request schema for approval response."""

    request_id: str = Field(..., min_length=1, max_length=100)
    approved: bool
    feedback: str | None = Field(None, max_length=1000)
    scope: Literal["once", "session"] = "once"


class CheckpointCaptureRequest(BaseModel):
    """Request schema for capturing a checkpoint."""

    label: str | None = Field(None, min_length=1, max_length=200)


class SessionResponse(BaseModel):
    """Response schema for session creation."""

    session_id: str


class SessionSummaryResponse(BaseModel):
    session_id: str
    id: str
    status: Literal[
        "created", "running", "waiting_approval", "completed", "failed", "closed"
    ]
    turn_status: Literal["idle", "running", "cancelling", "cancelled", "failed"]
    turn_id: str | None = None
    created_at: datetime
    updated_at: datetime
    last_activity: datetime
    turn_in_progress: bool
    pending_approval: bool
    provider_name: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    max_steps: int
    origin: dict[str, str] | None = None
    execution_binding: dict[str, object]
    workspace_id: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionSummaryResponse]


class ApprovalResponseSchema(BaseModel):
    """Response schema for approval endpoint."""

    status: str
    request_id: str
    decision: str


class CloseSessionResponse(BaseModel):
    """Response schema for session close."""

    status: str
    session_id: str


class CancelSessionResponse(BaseModel):
    """Response schema for session turn cancellation."""

    session_id: str
    turn_id: str | None = None
    status: Literal["idle", "cancelling", "cancelled", "failed"]


class SessionResultResponse(BaseModel):
    session_id: str
    status: Literal[
        "created", "running", "waiting_approval", "completed", "failed", "closed"
    ]
    turn_status: Literal["idle", "running", "cancelling", "cancelled", "failed"]
    turn_id: str | None = None
    workspace_id: str | None = None
    origin: dict[str, str] | None = None
    provider_name: str | None = None
    model_name: str | None = None
    final_answer: str | None = None
    verification_summary: str | None = None
    failure_details: str | None = None


class WorkspaceDiffFileSchema(BaseModel):
    path: str
    status: Literal["added", "modified", "deleted", "renamed", "binary", "unknown"]
    old_path: str | None = None
    additions: int | None = None
    deletions: int | None = None
    binary: bool = False


class WorkspaceDiffResponse(BaseModel):
    session_id: str
    workspace_id: str | None = None
    files: list[WorkspaceDiffFileSchema]
    additions: int
    deletions: int


class WorkspacePatchResponse(BaseModel):
    session_id: str
    workspace_id: str | None = None
    format: Literal["unified_diff"]
    patch: str


class PublishSessionRequest(BaseModel):
    mode: Literal["branch", "pr"]
    branch_name: str | None = Field(None, min_length=1, max_length=200)


class PublishSessionResponse(BaseModel):
    session_id: str
    mode: Literal["branch", "pr"]
    status: Literal["published", "unsupported", "failed"]
    branch_name: str | None = None
    pushed_ref: str | None = None
    commit_sha: str | None = None
    remote_url: str | None = None
    pr_url: str | None = None
    error: str | None = None


class CheckpointMetadataResponse(BaseModel):
    checkpoint_id: str
    tape_id: str
    session_id: str | None
    entry_count: int
    window_start: int
    created_at: datetime
    label: str | None = None


class CheckpointListResponse(BaseModel):
    checkpoints: list[CheckpointMetadataResponse]


class CheckpointRestoreResponse(BaseModel):
    status: str
    session_id: str
    checkpoint_id: str


class HealthResponse(BaseModel):
    """Response schema for health check."""

    status: str
    sessions: int
    version: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]


class WorkspaceArchiveResponse(BaseModel):
    format: Literal["tar.gz"]
    archive_base64: str


WorkspaceRetentionPolicy = Literal["delete_on_close", "ttl", "pinned", "manual"]


class WorkspaceSummarySchema(BaseModel):
    workspace_id: str
    status: Literal[
        "provisioning",
        "active",
        "idle",
        "retained",
        "stale",
        "cleaning",
        "cleaned",
        "cleanup_failed",
        "lost",
    ]
    updated_at: datetime
    session_id: str | None = None
    provider: str | None = None
    provider_instance_id: str | None = None
    workspace_host_label: str | None = None
    source_kind: str | None = None
    retention_policy: WorkspaceRetentionPolicy | None = None
    expires_at: datetime | None = None
    cleanup_error: str | None = None
    is_local: bool | None = None


class WorkspaceRetentionRequest(BaseModel):
    retention_policy: WorkspaceRetentionPolicy
    ttl_seconds: int | None = Field(None, gt=0)


class WorkspaceUnpinRequest(BaseModel):
    retention_policy: Literal["delete_on_close", "ttl"] | None = None
    ttl_seconds: int | None = Field(None, gt=0)


class WorkspaceRetentionResponse(BaseModel):
    workspace_id: str
    retention_policy: WorkspaceRetentionPolicy
    ttl_seconds: int | None = None
    status: Literal["retained", "unsupported"]


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceSummarySchema]


class WorkspaceCleanupResponse(BaseModel):
    workspace_id: str
    status: Literal["cleaned", "cleanup_failed"]
    error: str | None = None


class WorkspaceGcResponse(BaseModel):
    cleaned_count: int


class WorkspaceArchiveManifestResponse(BaseModel):
    workspace_id: str
    session_id: str | None = None
    format: Literal["tar.gz"]
    generated_at: datetime
    file_count: int
    total_bytes: int
    changed_files: list[str]
    deleted_files: list[str]
    excluded_files: list[str]
    archive_sha256: str | None = None
