"""Pydantic schemas for HTTP API request/response validation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, Field

from coding_agent.core.config import ProviderName


class LocalExecutionBindingRequest(BaseModel):
    kind: Literal["local"]
    workspace_root: str = Field(..., min_length=1, max_length=500)
    workspace_provider: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        pattern=r"^.*\S.*$",
    )
    provider_instance_id: str | None = Field(
        None,
        min_length=1,
        max_length=200,
        pattern=r"^.*\S.*$",
    )


class CloudWorkspaceBindingRequest(BaseModel):
    kind: Literal["cloud"]
    workspace_url: str = Field(..., min_length=1, max_length=500)
    workspace_id: str = Field(..., min_length=1, max_length=200)
    workspace_provider: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        pattern=r"^.*\S.*$",
    )
    provider_instance_id: str | None = Field(
        None,
        min_length=1,
        max_length=200,
        pattern=r"^.*\S.*$",
    )


class ExternalWorkerBindingRequest(BaseModel):
    kind: Literal["external_worker"]
    executor_kind: str = Field(..., min_length=1, max_length=100)
    worker_pool: str = Field("default", min_length=1, max_length=100)
    workspace_ref: dict[str, Any] | None = None
    provider_instance_id: str | None = Field(
        None,
        min_length=1,
        max_length=200,
        pattern=r"^.*\S.*$",
    )


class LocalAttachedExecutionBindingRequest(BaseModel):
    kind: Literal["local_attached"]
    executor_kind: str = Field(..., min_length=1, max_length=100)
    worker_pool: str = Field("default", min_length=1, max_length=100)
    workspace_ref: dict[str, Any] | None = None
    provider_instance_id: str | None = Field(
        None,
        min_length=1,
        max_length=200,
        pattern=r"^.*\S.*$",
    )


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


ExecutionBindingRequest = (
    LocalExecutionBindingRequest
    | CloudWorkspaceBindingRequest
    | LocalAttachedExecutionBindingRequest
    | ExternalWorkerBindingRequest
)
WorkspaceSourceRequest = DockerWorkspaceSourceRequest | GitWorkspaceSourceRequest


class PromptRequest(BaseModel):
    """Request schema for sending a prompt."""

    prompt: str = Field(..., min_length=1, max_length=10000)


class ResumeSessionRequest(BaseModel):
    """Request schema for resuming a session from durable context."""

    prompt: str | None = Field(None, min_length=1, max_length=10000)
    resume_reason: str = Field("user_resume", min_length=1, max_length=100)


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
    resumable: bool = False
    last_run_id: str | None = None
    last_run_status: str | None = None
    last_interrupted_run_id: str | None = None
    resume_from_event_id: str | None = None
    checkpoint_count: int = 0
    latest_checkpoint_id: str | None = None
    latest_checkpoint_label: str | None = None


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


class RuntimeRunResponse(BaseModel):
    run_id: str
    session_id: str
    tape_id: str | None = None
    parent_run_id: str | None = None
    agent_id: str | None = None
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    metadata: dict[str, Any]
    result: dict[str, Any]
    error: str | None = None


class RuntimeRunListResponse(BaseModel):
    session_id: str
    runs: list[RuntimeRunResponse]


class RuntimeMessageSnapshotResponse(BaseModel):
    snapshot_id: str
    run_id: str
    messages: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: datetime


class RuntimeEventResponse(BaseModel):
    sequence: int | None = None
    event_id: str
    run_id: str
    event_kind: str
    payload: dict[str, Any]
    created_at: datetime


class RuntimeEventsResponse(BaseModel):
    run_id: str
    events: list[RuntimeEventResponse]


class DisplayEventResponse(BaseModel):
    source_event_id: str
    run_id: str
    sequence: int | None = None
    display_kind: str
    payload: dict[str, Any]
    created_at: datetime


class DisplayEventsResponse(BaseModel):
    run_id: str
    events: list[DisplayEventResponse]


class WorkerClaimRequest(BaseModel):
    worker_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("worker_id", "executor_id"),
    )
    executor_kind: str = Field("local_cli", min_length=1, max_length=100)
    session_id: str | None = Field(None, min_length=1, max_length=100)
    lease_seconds: int = Field(30, ge=5, le=300)
    worker_instance_id: str | None = Field(None, min_length=1, max_length=200)
    process_id: int | None = Field(None, ge=1)
    capabilities: dict[str, Any] | None = None
    workspace_sync: dict[str, Any] | None = None


class WorkerClaimResponse(BaseModel):
    run_id: str
    session_id: str
    claim_token: str
    prompt: str
    tape_id: str | None = None
    approval_policy: str
    provider_name: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    max_steps: int


class WorkerHeartbeatRequest(BaseModel):
    worker_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("worker_id", "executor_id"),
    )
    claim_token: str = Field(..., min_length=1, max_length=500)
    lease_seconds: int = Field(30, ge=5, le=300)
    worker_instance_id: str | None = Field(None, min_length=1, max_length=200)
    process_id: int | None = Field(None, ge=1)
    capabilities: dict[str, Any] | None = None
    workspace_sync: dict[str, Any] | None = None


class WorkerHeartbeatResponse(BaseModel):
    run_id: str
    status: str
    cancel_requested: bool


class WorkerRuntimeEventRequest(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=200)
    event: str = Field(..., min_length=1, max_length=100)
    data: dict[str, Any]
    created_at: datetime | None = None


class WorkerRuntimeEventsRequest(BaseModel):
    worker_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("worker_id", "executor_id"),
    )
    claim_token: str = Field(..., min_length=1, max_length=500)
    events: list[WorkerRuntimeEventRequest] = Field(..., min_length=1, max_length=100)


class WorkerRunCompleteRequest(BaseModel):
    worker_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("worker_id", "executor_id"),
    )
    claim_token: str = Field(..., min_length=1, max_length=500)
    status: Literal["completed", "cancelled", "failed"] = "completed"
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(None, min_length=1, max_length=2000)
    tape_id: str | None = Field(None, min_length=1, max_length=200)
    tape_entries: list[dict[str, Any]] | None = None


class WorkerApprovalRequest(BaseModel):
    worker_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("worker_id", "executor_id"),
    )
    claim_token: str = Field(..., min_length=1, max_length=500)
    request_id: str = Field(..., min_length=1, max_length=100)
    tool_name: str = Field(..., min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(120, ge=1, le=3600)


class WorkerApprovalResponse(BaseModel):
    request_id: str
    approved: bool
    feedback: str | None = None
    scope: Literal["once", "session", "always"] = "once"


class WorkerStatusResponse(BaseModel):
    worker_id: str
    executor_id: str | None = None
    status: Literal["idle", "running", "stale", "offline"]
    executor_kind: str | None = None
    worker_pool: str | None = None
    worker_instance_id: str | None = None
    process_id: int | None = None
    capabilities: dict[str, Any] | None = None
    workspace_ref: dict[str, Any] | None = None
    workspace_sync: dict[str, Any] | None = None
    current_run_id: str | None = None
    current_session_id: str | None = None
    last_run_id: str | None = None
    last_session_id: str | None = None
    last_seen_at: datetime | None = None
    lease_expires_at: datetime | None = None


class WorkerListResponse(BaseModel):
    workers: list[WorkerStatusResponse]


class ExecutorListResponse(BaseModel):
    executors: list[WorkerStatusResponse]


class RuntimeInteractionResponse(BaseModel):
    interaction_id: str
    run_id: str
    interaction_kind: str
    status: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]
    metadata: dict[str, Any]
    created_at: datetime
    resolved_at: datetime | None = None


class RuntimeInteractionListResponse(BaseModel):
    interactions: list[RuntimeInteractionResponse]


class ResolveInteractionRequest(BaseModel):
    approved: bool
    feedback: str | None = Field(None, max_length=1000)
    scope: Literal["once", "session", "always"] = "once"


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
    status: Literal["published", "partial", "unsupported", "failed"]
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
    result_refs: dict[str, Any] | None = None
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
