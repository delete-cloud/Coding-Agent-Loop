"""Pydantic schemas for HTTP API request/response validation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    SecretStr,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from coding_agent.core.config import ProviderName

CODEX_ACCOUNT_LABEL_PATTERN = r"^[a-z0-9][a-z0-9-]{0,30}$"

_PROVIDER_NAME_VALUES = frozenset(get_args(ProviderName))


def validate_provider_value(value: str | None) -> str | None:
    """Allow ProviderName literals plus multi-account ``codex:<label>`` keys."""
    if value is None or value in _PROVIDER_NAME_VALUES:
        return value
    if value.startswith("codex:"):
        label = value.removeprefix("codex:")
        if re.fullmatch(CODEX_ACCOUNT_LABEL_PATTERN, label):
            return value
        raise ValueError(
            f"codex account label must match {CODEX_ACCOUNT_LABEL_PATTERN}: {value!r}"
        )
    raise ValueError(
        f"provider must be one of {sorted(_PROVIDER_NAME_VALUES)} "
        f"or 'codex:<label>', got {value!r}"
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


WorkspaceSourceRequest = DockerWorkspaceSourceRequest | GitWorkspaceSourceRequest


class _AdditiveChatPayload(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class _TextChatPayload(_AdditiveChatPayload):
    text: str = Field(..., min_length=1)


class _ProgressChatPayload(_AdditiveChatPayload):
    current: StrictInt = Field(..., ge=0)
    total: StrictInt = Field(..., ge=0)
    label: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _current_must_not_exceed_total(self) -> _ProgressChatPayload:
        if self.current > self.total:
            raise ValueError("progress current cannot exceed total")
        return self


class _ToolCallChatPayload(_AdditiveChatPayload):
    call_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any]


class _ToolResultChatPayload(_AdditiveChatPayload):
    call_id: str = Field(..., min_length=1)
    output: str
    is_error: StrictBool


class _ApprovalRequestedChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approval_request_id: str = Field(..., min_length=1)
    tool_call_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any]
    effect_id: str = Field(..., min_length=1)
    attempt_id: str = Field(..., min_length=1)
    target_run_id: str | None = Field(
        None,
        min_length=1,
        exclude_if=lambda value: value is None,
    )
    target_parent_effect_id: str | None = Field(
        None,
        min_length=1,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def _child_targets_must_appear_together(
        self,
    ) -> _ApprovalRequestedChatPayload:
        if (self.target_run_id is None) != (self.target_parent_effect_id is None):
            raise ValueError("approval child targets must appear together")
        return self


class _RootTerminalError(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class _RootTerminalChatPayload(_AdditiveChatPayload):
    outcome: Literal["completed", "failed", "cancelled", "interrupted"]
    result: str | None
    error: str | _RootTerminalError | None


ConnectedChatPayload = (
    _TextChatPayload
    | _ProgressChatPayload
    | _ToolCallChatPayload
    | _ToolResultChatPayload
    | _RootTerminalChatPayload
    | _ApprovalRequestedChatPayload
)


_CONNECTED_CHAT_PAYLOADS: dict[str, type[BaseModel]] = {
    "user_prompt": _TextChatPayload,
    "assistant_message": _TextChatPayload,
    "thinking": _TextChatPayload,
    "progress": _ProgressChatPayload,
    "tool_call": _ToolCallChatPayload,
    "tool_result": _ToolResultChatPayload,
    "approval_requested": _ApprovalRequestedChatPayload,
    "root_terminal": _RootTerminalChatPayload,
}


class ConnectedChatEventSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.1.0"]
    source_event_id: str = Field(..., min_length=1)
    session_seq: str = Field(..., pattern=r"^(0|[1-9][0-9]*)$")
    session_id: str = Field(..., min_length=1)
    run_id: str | None = Field(None, min_length=1)
    kind: Literal[
        "user_prompt",
        "assistant_message",
        "thinking",
        "progress",
        "tool_call",
        "tool_result",
        "root_terminal",
        "approval_requested",
    ]
    created_at: datetime
    payload: ConnectedChatPayload

    @field_validator("payload", mode="before")
    @classmethod
    def _validate_typed_payload(
        cls, value: Any, info: ValidationInfo
    ) -> ConnectedChatPayload:
        kind = info.data.get("kind")
        payload_type = _CONNECTED_CHAT_PAYLOADS.get(kind)
        if payload_type is None:
            raise ValueError("connected-chat kind must precede payload")
        return payload_type.model_validate(value)


class ConnectedChatStreamControlSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.1.0"]
    kind: Literal["replay_required"]
    reason: Literal[
        "subscriber_queue_overflow",
        "ownership_lost",
        "sequence_loss",
    ]
    cursor: str = Field(..., min_length=1)


class ConnectedChatSnapshotSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.1.0"]
    session_id: str = Field(..., min_length=1)
    projection: Literal["connected-chat"]
    projection_epoch: str = Field(..., pattern=r"^(0|[1-9][0-9]*)$")
    snapshot_cursor: str = Field(..., min_length=1)
    next_cursor: str | None = Field(None, min_length=1)
    events: list[ConnectedChatEventSchema]


class PromptRequest(BaseModel):
    """Request schema for sending a prompt."""

    prompt: str = Field(..., min_length=1, max_length=10000)
    command_id: str | None = Field(None, min_length=1, max_length=200)


class ResumeSessionRequest(BaseModel):
    """Request schema for resuming a session from durable context."""

    prompt: str | None = Field(None, min_length=1, max_length=10000)
    command_id: str | None = Field(None, min_length=1, max_length=200)
    parent_run_id: str | None = Field(None, max_length=200)
    resume_reason: str = Field("user_resume", min_length=1, max_length=100)


class ConnectedChatErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool
    replay_required: bool | None = None


class ConnectedChatErrorResponse(BaseModel):
    error: ConnectedChatErrorDetail


class ConnectedChatCancelResponse(BaseModel):
    contract_version: Literal["1.1.0"] = "1.1.0"
    session_id: str
    run_id: str
    status: Literal["cancelling"]


class CreateSessionRequest(BaseModel):
    """Request schema for creating a session."""

    model_config = ConfigDict(extra="forbid")

    repo_path: str | None = Field(None, max_length=500)
    default_run_target: dict[str, Any] | None = None
    run_target: dict[str, Any] | None = None
    workspace_source: WorkspaceSourceRequest | None = None
    approval_policy: str = Field("auto", pattern="^(yolo|interactive|auto)$")
    provider: str | None = None
    model: str | None = Field(None, min_length=1, max_length=200)
    base_url: str | None = Field(None, min_length=1, max_length=500)
    api_key: SecretStr | None = None
    max_steps: int | None = Field(None, ge=0)

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str | None) -> str | None:
        return validate_provider_value(value)


class ThinkingConfigSchema(BaseModel):
    """Thinking/reasoning configuration for provider API calls."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    effort: str = Field("medium", pattern="^(low|medium|high)$")


class RuntimeConfigUpdateRequest(BaseModel):
    """Request schema for updating session runtime config.

    Field semantics are three-state: omitted = leave unchanged, explicit
    null = reset to default (base_url only), value = set. model/provider/
    thinking have no meaningful reset target, so explicit null is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(None, min_length=1, max_length=200)
    provider: str | None = None
    api_key: SecretStr | None = None
    base_url: str | None = Field(None, min_length=1, max_length=500)
    thinking: ThinkingConfigSchema | None = None
    approval: str | None = Field(None, pattern="^(yolo|interactive|auto)$")

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str | None) -> str | None:
        return validate_provider_value(value)

    @field_validator("model", "provider", "thinking", "approval")
    @classmethod
    def _reject_explicit_null(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None:
            raise ValueError(
                f"{info.field_name} may not be null; omit the field to leave it unchanged"
            )
        return value


class RuntimeConfigUpdateResponse(BaseModel):
    """Response schema for runtime config update."""

    session_id: str
    provider_name: str | None
    model_name: str | None
    base_url: str | None


class MemoryReviewTransitionRequest(BaseModel):
    """Request schema for changing a reviewed-memory candidate state."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "rejected", "archived"]
    reason: str | None = Field(None, min_length=1, max_length=1000)


class MemoryReviewTransitionResponse(BaseModel):
    """Response schema for a reviewed-memory candidate transition."""

    candidate_id: str
    status: Literal["accepted", "rejected", "archived"]
    review_reason: str | None = None
    kind: str
    title: str
    scope: str
    tags: list[str]
    confidence: float


class MemoryReviewRecordResponse(BaseModel):
    """Response schema for listing reviewed-memory candidates."""

    candidate_id: str
    status: Literal["candidate", "accepted", "rejected", "archived"]
    review_reason: str | None = None
    kind: str
    title: str
    summary: str
    scope: str
    tags: list[str]
    confidence: float
    topic_id: str | None = None
    session_id: str | None = None
    tape_id: str | None = None


class SemanticMemoryStatusResponse(BaseModel):
    """Response schema for semantic memory maintenance status."""

    document_count: int = Field(..., ge=0)
    reviewed_memory_count: int = Field(..., ge=0)
    accepted_reviewed_memory_count: int = Field(..., ge=0)
    topic_store_available: bool


class SemanticMemoryRebuildRequest(BaseModel):
    """Request schema for semantic memory rebuild maintenance."""

    model_config = ConfigDict(extra="forbid")

    batch_size: StrictInt = Field(..., ge=1, le=1000)
    allow_rebuild: StrictBool = Field(
        ...,
        description="Allow semantic backend schema rebuild on schema mismatch.",
    )
    confirm_global: StrictBool = Field(
        ...,
        description="Explicitly confirm this rebuild clears the global semantic backend.",
    )

    @field_validator("confirm_global")
    @classmethod
    def _require_global_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "confirm_global must be true because semantic rebuild is global"
            )
        return value


class SemanticMemoryRebuildResponse(BaseModel):
    """Response schema for semantic memory rebuild maintenance."""

    scope: Literal["global"] = "global"
    topic_count: int = Field(..., ge=0)
    reviewed_memory_count: int = Field(..., ge=0)
    indexed_count: int = Field(..., ge=0)
    skipped_count: int = Field(..., ge=0)
    deleted_count: int = Field(..., ge=0)
    indexed_ids: list[str]
    deleted_ids: list[str]


class SemanticDogfoodTopicRequest(BaseModel):
    """Request schema for seeding one durable dogfood topic."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=256)
    summary: str = Field(..., min_length=1, max_length=256)
    kind: str = Field("coding", min_length=1, max_length=64)

    @field_validator("title", "summary", "kind")
    @classmethod
    def _reject_blank_strings(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value.strip()


class SemanticDogfoodTopicResponse(BaseModel):
    """Response schema for a seeded durable dogfood topic."""

    topic_id: str
    candidate_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


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
    title: str | None
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
    default_run_target: dict[str, object]
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
    contract_version: Literal["1.1.0"] = "1.1.0"
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


class ProviderModelSchema(BaseModel):
    """One model entry from a provider's live model listing."""

    id: str


class ProviderModelsResponse(BaseModel):
    """Response schema for the provider model-listing endpoint."""

    provider: str
    models: list[ProviderModelSchema]
    source: Literal["live", "unavailable"]

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        validated = validate_provider_value(value)
        assert validated is not None
        return validated


class CodexOAuthStartRequest(BaseModel):
    """Request schema for starting a codex device-flow login."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(None, pattern=CODEX_ACCOUNT_LABEL_PATTERN)


class CodexOAuthStartResponse(BaseModel):
    """Response schema for a started codex OAuth flow."""

    flow_id: str
    verification_url: str
    user_code: str
    expires_in: int


class CodexOAuthFlowResponse(BaseModel):
    """Response schema for one codex OAuth flow."""

    flow_id: str
    state: Literal["pending", "authorized", "error", "expired", "cancelled"]
    verification_url: str | None = None
    user_code: str | None = None
    account_label: str | None = None
    error: str | None = None


class CodexOAuthFlowListResponse(BaseModel):
    """Response schema for listing codex OAuth flows."""

    flows: list[CodexOAuthFlowResponse]


class CodexOAuthAccountResponse(BaseModel):
    """Response schema for one connected codex account."""

    provider: str
    label: str
    email: str | None = None
    plan: str | None = None
    connected_at: datetime | None = None


class CodexOAuthAccountListResponse(BaseModel):
    """Response schema for listing connected codex accounts."""

    accounts: list[CodexOAuthAccountResponse]


class CodexOAuthAccountDeleteResponse(BaseModel):
    """Response schema for disconnecting a codex account."""

    status: str
    provider: str


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
