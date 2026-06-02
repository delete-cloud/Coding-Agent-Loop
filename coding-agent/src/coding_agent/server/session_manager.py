"""SessionManager for managing agent sessions."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import secrets
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from inspect import isawaitable
from math import isfinite
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, TypeVar, cast

from agentkit.environment import Environment
from agentkit.storage.checkpoint_fs import FSCheckpointStore
from agentkit.storage.pg import PGPool
from agentkit.storage.sqlite import SQLiteCheckpointStore, SQLiteTapeStore
from agentkit.checkpoint.models import CheckpointMeta
from agentkit.checkpoint import CheckpointService
from agentkit.providers.models import DoneEvent, TextEvent
from agentkit.runtime import (
    DuplicateRuntimeMessageError,
    InMemoryRuntimeMessageBus,
    RuntimeMessage,
    RuntimeMessageBus,
    RuntimeMessageCursor,
    RuntimeMessageKind,
)
from agentkit.runtime.context import AgentRunContext
from agentkit.observability import ObservationSink
from agentkit.storage.protocols import CheckpointStore, TapeStore
from agentkit.storage.protocols import TapeDebugStore, TapeInfo, TapeSearchResult
from agentkit.tape.extract import TurnTrace, extract_turns
from agentkit.tape.tape import Tape
from agentkit.tools import FatalToolExecutionError
from agentkit.tape.models import Anchor, Entry
from coding_agent.adapter import PipelineAdapter
from coding_agent.agent_observability import (
    AgentObservationRecorder,
    AgentObservationStatus,
    AgentObservationStore,
    JsonlAgentObservationStore,
)
from coding_agent.approval import (
    ApprovalCoordinator,
    ApprovalDecisionConsumer,
    ApprovalDecisionConsumptionResult,
    ApprovalPolicy,
    approval_response_from_runtime_payload,
)
from coding_agent.approval.store import ApprovalStore
from coding_agent.core import config as core_config
from coding_agent.plugins.storage import JSONLTapeStore
from coding_agent.providers.base import ToolSchema
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONLRuntimeStore,
    JSONObject,
    PGRuntimeStore,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
    SQLiteRuntimeStore,
    JSONValue as RuntimeJSONValue,
)
from coding_agent.executors import (
    LocalDaemonExecutor,
    LocalDaemonRuntimeBinding,
    LocalDaemonRuntimeExecution,
    LocalDaemonRuntimePreparation,
)
from coding_agent.runs import (
    CloudWorkspaceRef,
    DefaultRunCoordinator,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunCoordinator,
    RunRequest,
    RunTarget,
    run_target_from_dict,
    run_target_from_execution_binding,
)
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    TurnEnd,
    WireMessage,
)
from coding_agent.server.stores.session_store import (
    SessionStore,
    create_session_store,
)
from coding_agent.server.stores.session_owner_store import SessionOwnerStoreProtocol
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictReason,
)
from coding_agent.environment.binding_resolver import (
    BindingResolver,
    DefaultBindingResolver,
)
from coding_agent.environment.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    ExternalWorkerBinding,
    LocalExecutionBinding,
)
from coding_agent.environment.local import LocalEnvironment
from coding_agent.server.stores.workspace_store import (
    JSONValue,
    WorkspaceRecord,
    WorkspaceRetentionPolicy,
    WorkspaceStatus,
)

logger = logging.getLogger(__name__)

_CHECKPOINT_SESSION_CONFIG_KEY = "session_restart_config"
_DEFAULT_EXECUTION_BINDING = object()
T = TypeVar("T")


def _approval_decision_message_id(session_id: str, request_id: str) -> str:
    return f"approval_decision:{session_id}:{request_id}"


def _hash_claim_token(claim_token: str) -> str:
    return hashlib.sha256(claim_token.encode("utf-8")).hexdigest()


def _metadata_required_str(metadata: Mapping[str, object], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"runtime run metadata is missing {key}")
    return value


def _optional_metadata_datetime(
    metadata: Mapping[str, object],
    key: str,
) -> datetime | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"runtime run metadata {key} must be a non-empty string")
    return datetime.fromisoformat(value)


def _subagent_message_id(session_id: str) -> str:
    return f"subagent_message:{session_id}:{uuid.uuid4().hex}"


def _approval_response_projection(response: ApprovalResponse) -> dict[str, Any]:
    return {
        "request_id": response.request_id,
        "decision": "approve" if response.approved else "deny",
        "feedback": response.feedback,
    }


def _approval_interaction_id(run_id: str, request_id: str) -> str:
    return f"{run_id}:approval:{request_id}"


def _json_compatible_value(value: object) -> RuntimeJSONValue:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return cast(RuntimeJSONValue, value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_compatible_value(item) for item in value]
    return str(value)


def _wire_message_event_payload(message: WireMessage) -> JSONObject:
    message_payload = _json_compatible_value(asdict(message))
    if not isinstance(message_payload, dict):
        raise TypeError("wire message payload must serialize to a JSON object")
    return {
        "message_type": type(message).__name__,
        "message": cast(RuntimeJSONValue, message_payload),
    }


def _runtime_event_correlation_from_run(run: AgentRunRecord) -> JSONObject:
    metadata = run.metadata
    payload: JSONObject = {
        "session_id": run.session_id,
        "run_id": run.run_id,
    }
    if run.tape_id is not None:
        payload["tape_id"] = run.tape_id
    for key in (
        "execution_placement",
        "execution_binding_kind",
        "workspace_surface",
        "execution_plane",
        "previous_run_id",
        "resume_from_run_id",
        "resume_from_event_id",
        "resume_reason",
        "resume_context_strategy",
        "resume_boundary_anchor_id",
        "resume_boundary_anchor_type",
        "executor_id",
        "worker_id",
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            payload[key] = value
    if metadata.get("resume_context_injected") is True:
        payload["resume_context_injected"] = True
    return payload


def _with_runtime_event_correlation(
    payload: JSONObject,
    correlation: JSONObject,
) -> JSONObject:
    return {**correlation, **payload}


def _approval_request_payload(request: ApprovalRequest) -> JSONObject:
    payload: JSONObject = {
        "session_id": request.session_id,
        "request_id": request.request_id,
        "timestamp": request.timestamp.isoformat(),
        "timeout_seconds": request.timeout_seconds,
    }
    if request.tool_call is not None:
        tool_call_payload = _json_compatible_value(asdict(request.tool_call))
        if not isinstance(tool_call_payload, dict):
            raise TypeError("approval tool_call payload must serialize to an object")
        payload["tool_call"] = cast(RuntimeJSONValue, tool_call_payload)
    return payload


def _approval_response_payload(response: ApprovalResponse) -> JSONObject:
    return {
        "session_id": response.session_id,
        "request_id": response.request_id,
        "approved": response.approved,
        "feedback": response.feedback,
        "scope": response.scope,
    }


def _approval_interaction_status(response: ApprovalResponse) -> str:
    return "approved" if response.approved else "rejected"


_STALE_RUNTIME_RUN_ERROR = "runtime run was still running during startup recovery"
_STALE_RUNTIME_RUN_RECOVERY_REASON = "startup_stale_running_run"
_RESUME_TAPE_TAIL_LIMIT = 5
_RESUME_CONTEXT_JSON_LIMIT = 4000
_RESUME_PLAN_NOTE_LIMIT = 1200
_RESUME_BOUNDARY_PRODUCT_ANCHOR_TYPE = "resume_boundary"
_RESUME_CONTEXT_STRATEGY = "checkpoint+tape_tail+message_snapshot"


def _runtime_message_snapshot(messages: object) -> list[JSONObject] | None:
    if messages is None:
        return None
    if not isinstance(messages, list):
        raise TypeError("runtime message snapshot source must be a list")
    payload = _json_compatible_value(messages)
    if not isinstance(payload, list):
        raise TypeError("runtime message snapshot must serialize to a JSON array")
    snapshot: list[JSONObject] = []
    for message in payload:
        if not isinstance(message, dict):
            raise TypeError("runtime message snapshot entries must be JSON objects")
        snapshot.append(cast(JSONObject, message))
    return snapshot


def _truncate_resume_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 14]}...[truncated]"


def _compact_resume_json(
    value: object, *, limit: int = _RESUME_CONTEXT_JSON_LIMIT
) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _truncate_resume_text(rendered, limit)


def _resume_tape_entry_summary(entry: Mapping[str, object]) -> JSONObject:
    kind = entry.get("kind")
    if not isinstance(kind, str) or not kind:
        raise TypeError("tape entry kind must be a non-empty string")
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("tape entry payload must be a JSON object")
    summary: JSONObject = {
        "kind": kind,
        "payload": cast(JSONObject, payload),
    }
    entry_id = entry.get("id")
    if isinstance(entry_id, str) and entry_id:
        summary["id"] = entry_id
    meta = entry.get("meta")
    if isinstance(meta, dict) and meta:
        summary["meta"] = cast(JSONObject, meta)
    anchor_type = entry.get("anchor_type")
    if isinstance(anchor_type, str) and anchor_type:
        summary["anchor_type"] = anchor_type
    return summary


def _entry_has_plan_signal(kind: str, payload: Mapping[str, object]) -> bool:
    kind_lower = kind.lower()
    if "plan" in kind_lower or "todo" in kind_lower:
        return True
    for key in payload:
        key_lower = key.lower()
        if key_lower in {"plan", "tasks", "todos"} or "plan" in key_lower:
            return True
    for key in ("tool_name", "name", "function", "tool"):
        value = payload.get(key)
        if isinstance(value, str) and value.lower() in {
            "plan",
            "planner",
            "todo_read",
            "todo_write",
        }:
            return True
    rendered = _compact_resume_json(payload, limit=_RESUME_PLAN_NOTE_LIMIT).lower()
    return (
        "todo_write" in rendered
        or "todo_read" in rendered
        or "current plan" in rendered
    )


def _latest_plan_note_from_tape_tail(
    tape_tail: tuple[JSONObject, ...],
) -> str | None:
    for entry in reversed(tape_tail):
        kind = entry.get("kind")
        payload = entry.get("payload")
        if not isinstance(kind, str) or not isinstance(payload, dict):
            continue
        if not _entry_has_plan_signal(kind, payload):
            continue
        return _compact_resume_json(entry, limit=_RESUME_PLAN_NOTE_LIMIT)
    return None


def _resume_prompt(
    resume_context: SessionResumeContext,
    *,
    prompt: str | None,
) -> str:
    user_prompt = (
        prompt if prompt is not None and prompt.strip() else _DEFAULT_RESUME_PROMPT
    )
    event_line = (
        f"Last known event id: {resume_context.resume_from_event_id}."
        if resume_context.resume_from_event_id is not None
        else "No runtime events were recorded for the previous run."
    )
    checkpoint_line = _resume_checkpoint_line(resume_context)
    message_snapshot_line = _resume_message_snapshot_line(resume_context)
    tape_tail_lines = _resume_tape_tail_lines(resume_context)
    plan_note_lines = _resume_plan_note_lines(resume_context)
    return "\n".join(
        [
            "Previous run was interrupted.",
            f"Previous run id: {resume_context.previous_run_id}.",
            f"Resume from run id: {resume_context.resume_from_run_id}.",
            event_line,
            checkpoint_line,
            message_snapshot_line,
            "Resume continues from current session history; it does not restore or roll back to a checkpoint.",
            *tape_tail_lines,
            *plan_note_lines,
            "Continue from the last known state.",
            "Do not repeat completed work unless needed.",
            "",
            "User resume request:",
            user_prompt,
        ]
    )


def _resume_message_snapshot_line(resume_context: SessionResumeContext) -> str:
    if resume_context.latest_message_snapshot_id is None:
        return "No runtime message snapshot is available for the previous run."
    count = resume_context.latest_message_snapshot_message_count
    count_text = f" ({count} messages)" if count is not None else ""
    return (
        "Latest runtime message snapshot: "
        f"{resume_context.latest_message_snapshot_id}{count_text}."
    )


def _resume_tape_tail_lines(resume_context: SessionResumeContext) -> list[str]:
    if not resume_context.tape_tail:
        return ["No tape tail is available for this session."]
    return [
        (
            f"Latest tape tail ({len(resume_context.tape_tail)} of "
            f"{resume_context.tape_entry_count} entries):"
        ),
        _compact_resume_json(list(resume_context.tape_tail)),
    ]


def _resume_plan_note_lines(resume_context: SessionResumeContext) -> list[str]:
    if resume_context.latest_plan_note is None:
        return ["No recent plan note was found in the tape tail."]
    return ["Latest plan/checkpoint note:", resume_context.latest_plan_note]


def _resume_checkpoint_line(resume_context: SessionResumeContext) -> str:
    if resume_context.latest_checkpoint_id is None:
        return "No checkpoint is available for this session."
    label = (
        f" ({resume_context.latest_checkpoint_label})"
        if resume_context.latest_checkpoint_label is not None
        else ""
    )
    return f"Latest checkpoint: {resume_context.latest_checkpoint_id}{label}."


def _resume_boundary_anchor_meta(
    resume_context: SessionResumeContext,
) -> JSONObject:
    metadata = resume_context.metadata()
    metadata["product_anchor_type"] = _RESUME_BOUNDARY_PRODUCT_ANCHOR_TYPE
    metadata["skip"] = True
    metadata["included_anchor_ids"] = []
    return metadata


@dataclass(frozen=True, slots=True)
class _PublishedApprovalDecision:
    sequence: int
    response: ApprovalResponse


TurnStatus = Literal["idle", "running", "cancelling", "cancelled", "failed"]
CancelTurnStatus = Literal["idle", "cancelling", "cancelled", "failed"]
_ACTIVE_RESUME_BLOCKING_RUN_STATUSES = {
    "queued",
    "requested",
    "claimed",
    "running",
    "cancelling",
}
_DEFAULT_RESUME_PROMPT = "Continue from the last known state."
_ATTACHED_EXECUTOR_BINDING_KINDS = {"external_worker", "local_attached"}


@dataclass(frozen=True)
class CancelTurnResult:
    session_id: str
    turn_id: str | None
    status: CancelTurnStatus


@dataclass(frozen=True)
class ExternalWorkerClaim:
    run: AgentRunRecord
    claim_token: str
    prompt: str
    session: Session


AttachedExecutorClaim = ExternalWorkerClaim


@dataclass(frozen=True)
class SessionResumeContext:
    previous_run_id: str
    resume_from_run_id: str
    resume_from_event_id: str | None
    resume_reason: str
    checkpoint_count: int = 0
    latest_checkpoint_id: str | None = None
    latest_checkpoint_label: str | None = None
    tape_entry_count: int = 0
    tape_tail: tuple[JSONObject, ...] = ()
    latest_plan_note: str | None = None
    latest_message_snapshot_id: str | None = None
    latest_message_snapshot_message_count: int | None = None
    resume_boundary_anchor_id: str | None = None
    resume_context_strategy: str = _RESUME_CONTEXT_STRATEGY

    def metadata(self) -> JSONObject:
        metadata: JSONObject = {
            "previous_run_id": self.previous_run_id,
            "resume_from_run_id": self.resume_from_run_id,
            "resume_reason": self.resume_reason,
            "resume_context_injected": True,
            "resume_context_strategy": self.resume_context_strategy,
            "checkpoint_count": self.checkpoint_count,
            "tape_entry_count": self.tape_entry_count,
            "resume_tape_tail_entry_count": len(self.tape_tail),
            "resume_plan_note_included": self.latest_plan_note is not None,
        }
        if self.resume_from_event_id is not None:
            metadata["resume_from_event_id"] = self.resume_from_event_id
        if self.latest_checkpoint_id is not None:
            metadata["latest_checkpoint_id"] = self.latest_checkpoint_id
        if self.latest_checkpoint_label is not None:
            metadata["latest_checkpoint_label"] = self.latest_checkpoint_label
        if self.latest_message_snapshot_id is not None:
            metadata["latest_message_snapshot_id"] = self.latest_message_snapshot_id
        if self.latest_message_snapshot_message_count is not None:
            metadata["latest_message_snapshot_message_count"] = (
                self.latest_message_snapshot_message_count
            )
        if self.resume_boundary_anchor_id is not None:
            metadata["resume_boundary_anchor_id"] = self.resume_boundary_anchor_id
            metadata["resume_boundary_anchor_type"] = (
                _RESUME_BOUNDARY_PRODUCT_ANCHOR_TYPE
            )
        return metadata


class WorkspaceMetadataStoreProtocol(Protocol):
    async def save(self, record: WorkspaceRecord) -> None: ...

    async def list(self) -> list[WorkspaceRecord]: ...

    async def load_by_workspace_id(
        self, workspace_id: str
    ) -> WorkspaceRecord | None: ...

    async def load_for_session_workspace(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceRecord | None: ...

    async def update_status(
        self,
        workspace_record_id: str,
        *,
        status: WorkspaceStatus,
        cleanup_error: str | None = None,
    ) -> None: ...

    async def update_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: WorkspaceRetentionPolicy,
        expires_at: datetime | None,
        status: WorkspaceStatus,
    ) -> None: ...

    async def update_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, JSONValue],
    ) -> None: ...


class RuntimeStoreProtocol(Protocol):
    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord: ...

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None: ...

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]: ...

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: JSONObject,
    ) -> AgentRunRecord | None: ...

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord: ...

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None: ...

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]: ...

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord: ...

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None: ...

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord: ...

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None: ...

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]: ...

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: JSONObject,
        resolved_at: datetime,
    ) -> AgentInteractionRecord: ...

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: JSONObject,
        result: JSONObject,
        error: str | None,
    ) -> AgentRunRecord: ...


class MockProvider:
    """Mock provider for testing that simulates LLM responses."""

    def __init__(self):
        self._max_context_size = 8192
        self._model_name = "mock"

    @property
    def max_context_size(self) -> int:
        """Maximum context size in tokens."""
        return self._max_context_size

    @property
    def model_name(self) -> str:
        """Name of the model being used."""
        return self._model_name

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        response_text = (
            "I'll help you with that request. Let me analyze the task... Done!"
        )

        for word in response_text.split():
            yield TextEvent(text=word + " ")
            await asyncio.sleep(0.01)

        yield DoneEvent()

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        """Return complete mock response."""
        return "Mock response"


@dataclass(frozen=True, slots=True)
class EventBroadcastResult:
    delivered_count: int
    full_pruned_count: int
    failed_pruned_count: int


class UnsupportedRuntimeExecutorError(RuntimeError):
    """Raised when a run target has no executor-backed runtime path."""


def _executor_ref_kind(executor: object) -> str:
    kind = getattr(executor, "kind", None)
    if isinstance(kind, str) and kind.strip():
        return kind
    return type(executor).__name__


@dataclass(frozen=True)
class _SessionLocalDaemonRuntimeProvider:
    prepare: Callable[[RunRequest], Awaitable[LocalDaemonRuntimeBinding]]

    async def prepare_runtime(self, request: RunRequest) -> LocalDaemonRuntimeBinding:
        return await self.prepare(request)


@dataclass
class SessionRuntimeHandle:
    """Process-local runtime state associated with a session record."""

    approval_coordinator: ApprovalCoordinator
    task: asyncio.Task[Any] | None = None
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    runtime_pipeline: Any | None = None
    runtime_ctx: Any | None = None
    runtime_adapter: Any | None = None
    runtime_message_bus: RuntimeMessageBus = field(
        default_factory=InMemoryRuntimeMessageBus
    )
    approval_decision_cursor: RuntimeMessageCursor = field(
        default_factory=RuntimeMessageCursor
    )


@dataclass(frozen=True)
class SessionRecord:
    """Durable session metadata stored across process restarts."""

    id: str
    created_at: datetime
    last_activity: datetime
    repo_path: Path | None
    origin: dict[str, str] | None
    execution_binding: ExecutionBinding
    default_run_target: RunTarget
    approval_policy: ApprovalPolicy
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    max_steps: int
    tape_id: str | None
    last_failure_details: str | None

    def to_store_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            # repo_path remains backward-compatible metadata and seeds the
            # default local binding when execution_binding is omitted.
            "repo_path": None if self.repo_path is None else str(self.repo_path),
            "origin": None if self.origin is None else dict(self.origin),
            "execution_binding": self.execution_binding.to_dict(),
            "default_run_target": self.default_run_target.to_dict(),
            "approval_policy": self.approval_policy.value,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
            "tape_id": self.tape_id,
            "last_failure_details": self.last_failure_details,
        }

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> SessionRecord:
        repo_path_raw = data.get("repo_path")
        if repo_path_raw is not None and not isinstance(repo_path_raw, str):
            raise TypeError("session metadata has invalid repo_path")
        origin_raw = data.get("origin")
        if origin_raw is not None:
            if not isinstance(origin_raw, dict):
                raise TypeError("session metadata has invalid origin")
            origin = {
                key: _required_session_str(cast(dict[str, Any], origin_raw), key)
                for key in cast(dict[str, Any], origin_raw)
            }
        else:
            origin = None
        approval_policy_raw = data.get("approval_policy")
        if not isinstance(approval_policy_raw, str):
            raise TypeError("session metadata is missing approval_policy")
        provider_name_raw = data.get("provider_name")
        if provider_name_raw is not None and not isinstance(provider_name_raw, str):
            raise TypeError("session metadata has invalid provider_name")
        model_name_raw = data.get("model_name")
        if model_name_raw is not None and not isinstance(model_name_raw, str):
            raise TypeError("session metadata has invalid model_name")
        base_url_raw = data.get("base_url")
        if base_url_raw is not None and not isinstance(base_url_raw, str):
            raise TypeError("session metadata has invalid base_url")
        tape_id_raw = data.get("tape_id")
        if tape_id_raw is not None and not isinstance(tape_id_raw, str):
            raise TypeError("session metadata has invalid tape_id")
        last_failure_details_raw = data.get("last_failure_details")
        if last_failure_details_raw is not None and not isinstance(
            last_failure_details_raw, str
        ):
            raise TypeError("session metadata has invalid last_failure_details")
        binding_raw = data.get("execution_binding")
        if binding_raw is not None:
            if not isinstance(binding_raw, dict):
                raise TypeError("session metadata has invalid execution_binding")
            execution_binding = ExecutionBinding.from_dict(binding_raw)
        else:
            workspace_root = (
                str(Path(repo_path_raw).resolve())
                if repo_path_raw is not None
                else str(Path.cwd().resolve())
            )
            execution_binding = LocalExecutionBinding(workspace_root=workspace_root)
        default_run_target_raw = data.get("default_run_target")
        if default_run_target_raw is None:
            default_run_target = run_target_from_execution_binding(execution_binding)
        else:
            if not isinstance(default_run_target_raw, dict):
                raise TypeError("session metadata has invalid default_run_target")
            default_run_target = run_target_from_dict(default_run_target_raw)
        return cls(
            id=_required_session_str(data, "id"),
            created_at=datetime.fromisoformat(
                _required_session_str(data, "created_at")
            ),
            last_activity=datetime.fromisoformat(
                _required_session_str(data, "last_activity")
            ),
            repo_path=None if repo_path_raw is None else Path(repo_path_raw),
            origin=origin,
            execution_binding=execution_binding,
            default_run_target=default_run_target,
            approval_policy=ApprovalPolicy(approval_policy_raw),
            provider_name=provider_name_raw,
            model_name=model_name_raw,
            base_url=base_url_raw,
            max_steps=_required_session_int(data, "max_steps"),
            tape_id=tape_id_raw,
            last_failure_details=last_failure_details_raw,
        )

    def to_session(self) -> Session:
        return Session(
            id=self.id,
            created_at=self.created_at,
            last_activity=self.last_activity,
            approval_store=ApprovalStore(),
            repo_path=self.repo_path,
            origin=self.origin,
            execution_binding=self.execution_binding,
            default_run_target=self.default_run_target,
            approval_policy=self.approval_policy,
            provider_name=self.provider_name,
            model_name=self.model_name,
            base_url=self.base_url,
            max_steps=self.max_steps,
            tape_id=self.tape_id,
            last_failure_details=self.last_failure_details,
        )


@dataclass
class Session:
    """A managed agent session.

    Note: ``execution_binding`` is the authoritative workspace contract.
    ``repo_path`` remains backward-compatible metadata and supplies the
    default local workspace root when no explicit execution binding is provided.
    """

    id: str
    created_at: datetime
    last_activity: datetime
    wire: LocalWire = field(init=False)
    approval_store: ApprovalStore = field(default_factory=ApprovalStore)
    repo_path: Path | None = None  # legacy metadata; seeds default local binding
    origin: dict[str, str] | None = None
    execution_binding: ExecutionBinding = field(  # type: ignore[assignment]
        default=cast(ExecutionBinding, _DEFAULT_EXECUTION_BINDING),
    )
    default_run_target: RunTarget | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: Any | None = None
    provider_name: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    max_steps: int = 30
    runtime_handle: SessionRuntimeHandle = field(init=False, repr=False)
    task: asyncio.Task[Any] | None = None
    turn_in_progress: bool = False
    turn_status: TurnStatus = "idle"
    current_turn_id: str | None = None
    last_failure_details: str | None = None
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    tape_id: str | None = None
    runtime_pipeline: Any | None = None
    runtime_ctx: Any | None = None
    runtime_adapter: Any | None = None
    runtime_message_bus: RuntimeMessageBus = field(
        default_factory=InMemoryRuntimeMessageBus
    )
    approval_decision_cursor: RuntimeMessageCursor = field(
        default_factory=RuntimeMessageCursor
    )
    approval_coordinator: ApprovalCoordinator = field(init=False)

    _RUNTIME_HANDLE_FIELD_NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "task",
            "pending_approval",
            "approval_event",
            "approval_response",
            "event_queues",
            "runtime_pipeline",
            "runtime_ctx",
            "runtime_adapter",
            "runtime_message_bus",
            "approval_decision_cursor",
            "approval_coordinator",
        }
    )

    def __getattribute__(self, name: str) -> Any:
        if name in object.__getattribute__(self, "_RUNTIME_HANDLE_FIELD_NAMES"):
            instance_dict = object.__getattribute__(self, "__dict__")
            handle = instance_dict.get("runtime_handle")
            if handle is not None:
                return getattr(handle, name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "approval_store":
            object.__setattr__(self, name, value)
            instance_dict = object.__getattribute__(self, "__dict__")
            handle = instance_dict.get("runtime_handle")
            if handle is not None:
                handle.approval_coordinator = ApprovalCoordinator(
                    cast(ApprovalStore, value)
            )
            return
        if name == "execution_binding":
            object.__setattr__(self, name, value)
            if value is not cast(ExecutionBinding, _DEFAULT_EXECUTION_BINDING):
                object.__setattr__(
                    self,
                    "default_run_target",
                    run_target_from_execution_binding(cast(ExecutionBinding, value)),
                )
            return
        if name in self._RUNTIME_HANDLE_FIELD_NAMES:
            instance_dict = object.__getattribute__(self, "__dict__")
            handle = instance_dict.get("runtime_handle")
            if handle is not None:
                setattr(handle, name, value)
                return
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if self.execution_binding is cast(ExecutionBinding, _DEFAULT_EXECUTION_BINDING):
            workspace_root = (
                str(self.repo_path.resolve())
                if self.repo_path is not None
                else str(Path.cwd().resolve())
            )
            self.execution_binding = LocalExecutionBinding(
                workspace_root=workspace_root
            )
        if self.default_run_target is None:
            self.default_run_target = run_target_from_execution_binding(
                self.execution_binding
            )
        self.wire = LocalWire(self.id)
        handle = SessionRuntimeHandle(
            approval_coordinator=ApprovalCoordinator(self.approval_store),
            task=self.task,
            pending_approval=self.pending_approval,
            approval_event=self.approval_event,
            approval_response=self.approval_response,
            event_queues=self.event_queues,
            runtime_pipeline=self.runtime_pipeline,
            runtime_ctx=self.runtime_ctx,
            runtime_adapter=self.runtime_adapter,
            runtime_message_bus=self.runtime_message_bus,
            approval_decision_cursor=self.approval_decision_cursor,
        )
        object.__setattr__(self, "runtime_handle", handle)
        instance_dict = object.__getattribute__(self, "__dict__")
        for field_name in self._RUNTIME_HANDLE_FIELD_NAMES:
            instance_dict.pop(field_name, None)

    def broadcast_event_nowait(self, event: dict[str, Any]) -> EventBroadcastResult:
        active_queues: list[asyncio.Queue[dict[str, Any]]] = []
        delivered_count = 0
        full_pruned_count = 0
        failed_pruned_count = 0

        for queue in self.event_queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                full_pruned_count += 1
            except Exception:
                logger.debug(
                    "Pruning event queue after broadcast failure",
                    exc_info=True,
                )
                failed_pruned_count += 1
            else:
                delivered_count += 1
                active_queues.append(queue)

        self.event_queues = active_queues
        return EventBroadcastResult(
            delivered_count=delivered_count,
            full_pruned_count=full_pruned_count,
            failed_pruned_count=failed_pruned_count,
        )

    def as_dict(self) -> dict[str, Any]:
        workspace_id = (
            self.execution_binding.workspace_id
            if isinstance(self.execution_binding, CloudWorkspaceBinding)
            else None
        )
        pending_approval = self.pending_approval is not None
        turn_running = self.turn_in_progress or (
            self.task is not None and not self.task.done()
        )
        if self.turn_status in {"cancelling", "cancelled", "failed"}:
            turn_status = self.turn_status
        elif turn_running:
            turn_status = "running"
        else:
            turn_status = "idle"
        if pending_approval:
            status = "waiting_approval"
        elif turn_status in {"running", "cancelling"}:
            status = "running"
        elif turn_status == "failed":
            status = "failed"
        else:
            status = "created"
        return {
            "id": self.id,
            "session_id": self.id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.last_activity.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "turn_in_progress": self.turn_in_progress,
            "pending_approval": pending_approval,
            "status": status,
            "turn_status": turn_status,
            "turn_id": self.current_turn_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
            "origin": None if self.origin is None else dict(self.origin),
            "execution_binding": self.execution_binding.to_dict(),
            "default_run_target": self.default_run_target.to_dict(),
            "workspace_id": workspace_id,
        }

    def to_store_data(self) -> dict[str, Any]:
        return self.to_record().to_store_data()

    def to_record(self) -> SessionRecord:
        return SessionRecord(
            id=self.id,
            created_at=self.created_at,
            last_activity=self.last_activity,
            repo_path=self.repo_path,
            origin=None if self.origin is None else dict(self.origin),
            execution_binding=self.execution_binding,
            default_run_target=self.default_run_target,
            approval_policy=self.approval_policy,
            provider_name=self.provider_name,
            model_name=self.model_name,
            base_url=self.base_url,
            max_steps=self.max_steps,
            tape_id=self.tape_id,
            last_failure_details=self.last_failure_details,
        )

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> Session:
        session = SessionRecord.from_store_data(data).to_session()
        session.turn_in_progress = False
        session.pending_approval = None
        session.approval_response = None
        return session


@dataclass(frozen=True)
class _CheckpointSessionConfig:
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    max_steps: int
    approval_policy: ApprovalPolicy


def _required_session_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"session metadata is missing {key}")
    return value


def _required_session_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise TypeError(f"session metadata is missing {key}")
    return value


def _serialize_checkpoint_session_config(session: Session) -> dict[str, Any]:
    return {
        "provider_name": session.provider_name,
        "model_name": session.model_name,
        "base_url": session.base_url,
        "max_steps": session.max_steps,
        "approval_policy": session.approval_policy.value,
    }


def _checkpoint_session_config_from_extra(
    session: Session, extra: dict[str, Any]
) -> _CheckpointSessionConfig:
    raw = extra.get(_CHECKPOINT_SESSION_CONFIG_KEY)
    if raw is None:
        return _CheckpointSessionConfig(
            provider_name=session.provider_name,
            model_name=session.model_name,
            base_url=session.base_url,
            max_steps=session.max_steps,
            approval_policy=session.approval_policy,
        )
    if not isinstance(raw, dict):
        raise TypeError("checkpoint session config must be an object")

    required_keys = {
        "provider_name",
        "model_name",
        "base_url",
        "max_steps",
        "approval_policy",
    }
    missing_keys = sorted(required_keys - raw.keys())
    if missing_keys:
        missing = ", ".join(missing_keys)
        raise TypeError(f"checkpoint session config is missing {missing}")

    provider_name = raw.get("provider_name")
    if provider_name is not None and not isinstance(provider_name, str):
        raise TypeError("checkpoint session config has invalid provider_name")

    model_name = raw.get("model_name")
    if model_name is not None and not isinstance(model_name, str):
        raise TypeError("checkpoint session config has invalid model_name")

    base_url = raw.get("base_url")
    if base_url is not None and not isinstance(base_url, str):
        raise TypeError("checkpoint session config has invalid base_url")

    max_steps = raw.get("max_steps")
    if not isinstance(max_steps, int):
        raise TypeError("checkpoint session config has invalid max_steps")

    approval_policy_raw = raw.get("approval_policy")
    if not isinstance(approval_policy_raw, str):
        raise TypeError("checkpoint session config has invalid approval_policy")

    return _CheckpointSessionConfig(
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
        max_steps=max_steps,
        approval_policy=ApprovalPolicy(approval_policy_raw),
    )


def _load_pg_storage_types() -> tuple[Any, Any, Any]:
    try:
        pg_module = importlib.import_module("agentkit.storage.pg")
    except ImportError as exc:
        raise RuntimeError(
            "PG backend is not available; ensure agentkit.storage.pg and its PostgreSQL "
            "optional dependencies are installed before using tape_backend='pg' "
            "(for example, install/include the PG extra or `asyncpg`)."
        ) from exc
    required_symbols = ("PGPool", "PGTapeStore", "PGCheckpointStore")
    missing_symbols = [
        symbol for symbol in required_symbols if not hasattr(pg_module, symbol)
    ]
    if missing_symbols:
        raise RuntimeError(
            "PG backend is missing required exports from agentkit.storage.pg: "
            f"{', '.join(missing_symbols)}. Ensure the installed PG backend package "
            "version includes the PostgreSQL storage implementation and its optional "
            "dependencies."
        )
    return (
        getattr(pg_module, "PGPool"),
        getattr(pg_module, "PGTapeStore"),
        getattr(pg_module, "PGCheckpointStore"),
    )


class _WireConsumer:
    def __init__(
        self,
        wire: LocalWire,
        approval_handler: Any,
        emit_handler: Callable[[WireMessage], Awaitable[None]] | None = None,
    ) -> None:
        self._wire = wire
        self._approval_handler = approval_handler
        self._emit_handler = emit_handler

    async def emit(self, msg: WireMessage) -> None:
        if self._emit_handler is not None:
            await self._emit_handler(msg)
            return
        await self._wire.send(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return await self._approval_handler(req)


class SessionManager:
    """Manages agent sessions with lifecycle and resource management."""

    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        storage_config: dict[str, Any] | None = None,
        pg_pool: object | None = None,
        tape_store: TapeStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
        checkpoint_service: CheckpointService | None = None,
        create_agent_fn: Callable[..., tuple[Any, Any]] | None = None,
        binding_resolver: BindingResolver | None = None,
        provisioned_cloud_binding_cleanup: (
            Callable[[CloudWorkspaceBinding], None] | None
        ) = None,
        workspace_metadata_store: WorkspaceMetadataStoreProtocol | None = None,
        runtime_store: RuntimeStoreProtocol | None = None,
        observation_store: AgentObservationStore | None = None,
        owner_store: SessionOwnerStoreProtocol | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
        owner_lease_seconds: float = 30.0,
        run_coordinator: RunCoordinator | None = None,
        local_daemon_executor: LocalDaemonExecutor | None = None,
    ):
        self._storage_config = storage_config or {}
        self._pg_pool = pg_pool
        self._owns_pg_pool = False
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        self._store = store or self._create_http_session_store()
        self._session_cache: dict[str, Session] = {}
        self._approval_stores: dict[str, ApprovalStore] = {}
        self._lock = asyncio.Lock()
        self._store_io_guard = threading.Lock()
        self._session_turn_locks: dict[str, asyncio.Lock] = {}
        self._session_workspace_export_counts: dict[str, int] = {}
        self._tape_store = tape_store or self._create_tape_store(data_dir)
        self._agent_observation_store = observation_store or JsonlAgentObservationStore(
            data_dir / "observability"
        )
        resolved_checkpoint_store = checkpoint_store or self._create_checkpoint_store(
            data_dir
        )
        self._checkpoint_service = checkpoint_service or CheckpointService(
            resolved_checkpoint_store
        )
        self._create_agent = create_agent_fn
        self._binding_resolver = binding_resolver or DefaultBindingResolver()
        self._local_daemon_executor = (
            LocalDaemonExecutor()
            if local_daemon_executor is None
            else local_daemon_executor
        )
        self._run_coordinator = (
            DefaultRunCoordinator(local_daemon_executor=self._local_daemon_executor)
            if run_coordinator is None
            else run_coordinator
        )
        self._provisioned_cloud_binding_cleanup = provisioned_cloud_binding_cleanup
        self._workspace_metadata_store = workspace_metadata_store
        self._runtime_store = (
            runtime_store if runtime_store is not None else self._create_runtime_store()
        )
        self.configure_owner_leases(
            owner_store=owner_store,
            owner_id=owner_id,
            fencing_token=fencing_token,
            owner_lease_seconds=owner_lease_seconds,
        )

    @property
    def owner_lease_seconds(self) -> float:
        return self._owner_lease_seconds

    @property
    def has_owner_leases_configured(self) -> bool:
        return self._owner_store is not None

    @property
    def pg_pool(self) -> PGPool:
        return self._get_pg_pool()

    def configure_owner_leases(
        self,
        *,
        owner_store: SessionOwnerStoreProtocol | None,
        owner_id: str | None,
        fencing_token: int | None,
        owner_lease_seconds: float = 30.0,
    ) -> None:
        if owner_store is None and (owner_id is not None or fencing_token is not None):
            raise ValueError(
                "owner_store must be provided when owner_id or fencing_token is set"
            )
        if owner_store is not None and (owner_id is None or fencing_token is None):
            raise ValueError(
                "owner_id and fencing_token must be provided when owner_store is set"
            )
        if owner_lease_seconds <= 0:
            raise ValueError("owner_lease_seconds must be positive")
        self._owner_store = owner_store
        self._owner_id = owner_id
        self._fencing_token = fencing_token
        self._owner_lease_seconds = owner_lease_seconds

    def configure_workspace_metadata_store(
        self,
        workspace_metadata_store: WorkspaceMetadataStoreProtocol | None,
    ) -> None:
        self._workspace_metadata_store = workspace_metadata_store

    def configure_runtime_store(
        self,
        runtime_store: RuntimeStoreProtocol | None,
    ) -> None:
        self._runtime_store = runtime_store

    def configure_run_coordinator(self, run_coordinator: RunCoordinator) -> None:
        self._run_coordinator = run_coordinator

    def _require_runtime_store(self) -> RuntimeStoreProtocol:
        if self._runtime_store is None:
            raise RuntimeError("runtime store is not configured")
        return self._runtime_store

    async def load_runtime_run(self, run_id: str) -> AgentRunRecord:
        store = self._require_runtime_store()
        record = await store.load_agent_run(run_id)
        if record is None:
            raise KeyError(f"runtime run not found: {run_id}")
        return record

    async def list_runtime_runs(self, session_id: str) -> list[AgentRunRecord]:
        store = self._require_runtime_store()
        return await store.list_agent_runs(session_id)

    async def session_resume_metadata(self, session_id: str) -> dict[str, Any]:
        session = await self.get_session_async(session_id)
        metadata: dict[str, Any] = {
            "resumable": False,
            "last_run_id": None,
            "last_run_status": None,
            "last_interrupted_run_id": None,
            "resume_from_event_id": None,
            "checkpoint_count": 0,
            "latest_checkpoint_id": None,
            "latest_checkpoint_label": None,
        }
        try:
            latest_run = await self._latest_runtime_run(session_id)
        except RuntimeError:
            latest_run = None
        if latest_run is not None:
            metadata["last_run_id"] = latest_run.run_id
            metadata["last_run_status"] = latest_run.status
            metadata["resumable"] = (
                latest_run.status not in _ACTIVE_RESUME_BLOCKING_RUN_STATUSES
            )
            metadata["resume_from_event_id"] = await self._latest_runtime_event_id(
                latest_run
            )
            interrupted_runs = [
                run
                for run in await self.list_runtime_runs(session_id)
                if run.status == "interrupted"
            ]
            if interrupted_runs:
                metadata["last_interrupted_run_id"] = max(
                    interrupted_runs,
                    key=lambda run: (run.started_at, run.run_id),
                ).run_id
        if session.tape_id is not None:
            checkpoints = await self.list_checkpoints(session_id)
            metadata["checkpoint_count"] = len(checkpoints)
            if checkpoints:
                latest_checkpoint = max(
                    checkpoints,
                    key=lambda checkpoint: (
                        checkpoint.created_at,
                        checkpoint.checkpoint_id,
                    ),
                )
                metadata["latest_checkpoint_id"] = latest_checkpoint.checkpoint_id
                metadata["latest_checkpoint_label"] = latest_checkpoint.label
        return metadata

    async def _latest_runtime_run(self, session_id: str) -> AgentRunRecord | None:
        runs = await self.list_runtime_runs(session_id)
        if not runs:
            return None
        return max(runs, key=lambda run: (run.started_at, run.run_id))

    async def _latest_runtime_event_id(self, run: AgentRunRecord) -> str | None:
        events = await self.replay_runtime_events(run.run_id, limit=1000)
        if not events:
            return None
        sequenced_events = [event for event in events if event.sequence is not None]
        if sequenced_events:
            return max(sequenced_events, key=lambda event: event.sequence or 0).event_id
        return max(events, key=lambda event: event.created_at).event_id

    async def list_runtime_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        store = self._require_runtime_store()
        return await store.list_agent_interactions(run_id)

    async def load_runtime_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord:
        store = self._require_runtime_store()
        record = await store.load_agent_interaction(interaction_id)
        if record is None:
            raise KeyError(f"runtime interaction not found: {interaction_id}")
        return record

    async def load_tape_debug_info(self, tape_id: str) -> TapeInfo | None:
        if not isinstance(self._tape_store, TapeDebugStore):
            return None
        return await self._tape_store.info(tape_id)

    async def search_tape_debug_entries(
        self,
        *,
        tape_id: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        tool_call_id: str | None = None,
        anchor_type: str | None = None,
        limit: int = 100,
    ) -> list[TapeSearchResult]:
        if not isinstance(self._tape_store, TapeDebugStore):
            return []
        return await self._tape_store.search(
            tape_id=tape_id,
            kind=kind,
            run_id=run_id,
            tool_call_id=tool_call_id,
            anchor_type=anchor_type,
            limit=limit,
        )

    async def load_runtime_message_snapshot(
        self,
        run_id: str,
    ) -> RunMessageSnapshotRecord:
        store = self._require_runtime_store()
        snapshot_id = f"{run_id}:latest"
        record = await store.load_message_snapshot(snapshot_id)
        if record is None:
            raise KeyError(f"runtime message snapshot not found: {snapshot_id}")
        return record

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        store = self._require_runtime_store()
        after_sequence = 0
        if last_event_id is not None:
            last_event = await store.load_runtime_event(last_event_id)
            if last_event is None or last_event.run_id != run_id:
                raise KeyError(f"runtime event not found: {last_event_id}")
            if last_event.sequence is None:
                raise RuntimeError(
                    f"runtime event has no replay sequence: {last_event_id}"
                )
            after_sequence = last_event.sequence
        return await store.replay_runtime_events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def request_attached_executor_run(
        self,
        session_id: str,
        prompt: str,
        *,
        run_id: str | None = None,
        resume_context: SessionResumeContext | None = None,
    ) -> AgentRunRecord:
        store = self._require_runtime_store()
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            if not isinstance(session.execution_binding, ExternalWorkerBinding):
                raise ValueError("session does not use attached executor execution")
            if session.turn_in_progress or session.turn_status in {
                "running",
                "cancelling",
            }:
                raise RuntimeError("turn already in progress")
            resolved_run_id = run_id or uuid.uuid4().hex
            now = datetime.now(UTC)
            metadata = self._run_metadata_for_session(
                session,
                resume_context=resume_context,
            )
            metadata["prompt"] = prompt
            metadata["requested_at"] = now.isoformat()
            metadata["run_request_status"] = "requested"
            record = await store.create_agent_run(
                AgentRunRecord(
                    run_id=resolved_run_id,
                    session_id=session.id,
                    tape_id=session.tape_id,
                    parent_run_id=(
                        None
                        if resume_context is None
                        else resume_context.previous_run_id
                    ),
                    agent_id=None,
                    status="requested",
                    started_at=now,
                    metadata=metadata,
                    result={},
                    error=None,
                )
            )
            session.current_turn_id = resolved_run_id
            session.turn_in_progress = True
            session.turn_status = "running"
            session.last_activity = now
            session.last_failure_details = None
            await self._persist_session_async(session)
            return record

    async def request_external_worker_run(
        self,
        session_id: str,
        prompt: str,
        *,
        run_id: str | None = None,
        resume_context: SessionResumeContext | None = None,
    ) -> AgentRunRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.request_attached_executor_run(
            session_id,
            prompt,
            run_id=run_id,
            resume_context=resume_context,
        )

    async def resume_session(
        self,
        session_id: str,
        *,
        prompt: str | None = None,
        resume_reason: str = "user_resume",
    ) -> AgentRunRecord:
        if not resume_reason.strip():
            raise ValueError("resume_reason must be non-empty")
        store = self._require_runtime_store()
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        if session.turn_in_progress or session.turn_status in {
            "running",
            "cancelling",
        }:
            raise RuntimeError("turn already in progress")
        previous_run = await self._latest_runtime_run(session_id)
        if previous_run is None:
            raise RuntimeError("session has no previous run to resume")
        if previous_run.status in _ACTIVE_RESUME_BLOCKING_RUN_STATUSES:
            raise RuntimeError("latest run is still active")
        if session.tape_id is None and previous_run.tape_id is not None:
            session.tape_id = previous_run.tape_id
            await self._persist_session_async(session)
        checkpoint_context = await self._latest_resume_checkpoint_context(session)
        tape_context = await self._latest_resume_tape_context(session)
        message_snapshot_context = await self._latest_resume_message_snapshot_context(
            previous_run
        )
        resume_context = SessionResumeContext(
            previous_run_id=previous_run.run_id,
            resume_from_run_id=previous_run.run_id,
            resume_from_event_id=await self._latest_runtime_event_id(previous_run),
            resume_reason=resume_reason,
            checkpoint_count=checkpoint_context["checkpoint_count"],
            latest_checkpoint_id=checkpoint_context["latest_checkpoint_id"],
            latest_checkpoint_label=checkpoint_context["latest_checkpoint_label"],
            tape_entry_count=tape_context["tape_entry_count"],
            tape_tail=tape_context["tape_tail"],
            latest_plan_note=tape_context["latest_plan_note"],
            latest_message_snapshot_id=message_snapshot_context[
                "latest_message_snapshot_id"
            ],
            latest_message_snapshot_message_count=message_snapshot_context[
                "latest_message_snapshot_message_count"
            ],
        )
        resume_context = await self._append_resume_boundary_anchor(
            session,
            resume_context,
        )
        resume_prompt = _resume_prompt(
            resume_context,
            prompt=prompt,
        )
        run_id = uuid.uuid4().hex
        if isinstance(session.execution_binding, ExternalWorkerBinding):
            return await self.request_attached_executor_run(
                session_id,
                resume_prompt,
                run_id=run_id,
                resume_context=resume_context,
            )
        await self.run_agent(
            session_id,
            resume_prompt,
            run_id_override=run_id,
            resume_context=resume_context,
        )
        record = await store.load_agent_run(run_id)
        if record is None:
            raise RuntimeError(f"resumed runtime run was not recorded: {run_id}")
        return record

    async def _append_resume_boundary_anchor(
        self,
        session: Session,
        resume_context: SessionResumeContext,
    ) -> SessionResumeContext:
        if not session.tape_id:
            raise RuntimeError("session tape_id is required to append resume boundary")
        anchor = Anchor(
            anchor_type="context",
            payload={"label": "Resume boundary"},
            meta=_resume_boundary_anchor_meta(resume_context),
        )
        await self._tape_store.save(session.tape_id, [anchor.to_dict()])
        runtime_ctx = session.runtime_ctx
        tape = getattr(runtime_ctx, "tape", None)
        if isinstance(tape, Tape) and tape.tape_id == session.tape_id:
            tape.append(anchor)
        return replace(resume_context, resume_boundary_anchor_id=anchor.id)

    async def _latest_resume_checkpoint_context(
        self,
        session: Session,
    ) -> dict[str, Any]:
        if session.tape_id is None:
            return {
                "checkpoint_count": 0,
                "latest_checkpoint_id": None,
                "latest_checkpoint_label": None,
            }
        checkpoints = await self.list_checkpoints(session.id)
        if not checkpoints:
            return {
                "checkpoint_count": 0,
                "latest_checkpoint_id": None,
                "latest_checkpoint_label": None,
            }
        latest_checkpoint = max(
            checkpoints,
            key=lambda checkpoint: (checkpoint.created_at, checkpoint.checkpoint_id),
        )
        return {
            "checkpoint_count": len(checkpoints),
            "latest_checkpoint_id": latest_checkpoint.checkpoint_id,
            "latest_checkpoint_label": latest_checkpoint.label,
        }

    async def _latest_resume_tape_context(
        self,
        session: Session,
    ) -> dict[str, Any]:
        if session.tape_id is None:
            return {
                "tape_entry_count": 0,
                "tape_tail": (),
                "latest_plan_note": None,
            }
        entries = await self._tape_store.load(session.tape_id)
        tail_entries = entries[-_RESUME_TAPE_TAIL_LIMIT:]
        tape_tail = tuple(
            _resume_tape_entry_summary(cast(Mapping[str, object], entry))
            for entry in tail_entries
        )
        return {
            "tape_entry_count": len(entries),
            "tape_tail": tape_tail,
            "latest_plan_note": _latest_plan_note_from_tape_tail(tape_tail),
        }

    async def _latest_resume_message_snapshot_context(
        self,
        previous_run: AgentRunRecord,
    ) -> dict[str, Any]:
        store = self._require_runtime_store()
        snapshot_id = f"{previous_run.run_id}:latest"
        snapshot = await store.load_message_snapshot(snapshot_id)
        if snapshot is None:
            return {
                "latest_message_snapshot_id": None,
                "latest_message_snapshot_message_count": None,
            }
        return {
            "latest_message_snapshot_id": snapshot.snapshot_id,
            "latest_message_snapshot_message_count": len(snapshot.messages),
        }

    async def claim_attached_executor_run(
        self,
        *,
        executor_id: str,
        executor_kind: str,
        session_id: str | None = None,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> ExternalWorkerClaim | None:
        store = self._require_runtime_store()
        claim_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        claim_metadata: JSONObject = {
            "worker_id": executor_id,
            "executor_id": executor_id,
            "claim_token_hash": _hash_claim_token(claim_token),
            "claimed_at": now.isoformat(),
            "lease_expires_at": lease_expires_at.isoformat(),
        }
        if worker_instance_id is not None:
            claim_metadata["worker_instance_id"] = worker_instance_id
        if process_id is not None:
            claim_metadata["process_id"] = process_id
        if capabilities is not None:
            claim_metadata["capabilities"] = capabilities
        if workspace_sync is not None:
            claim_metadata["workspace_sync"] = workspace_sync
        run = await store.claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )
        if run is None:
            return None
        session = await self.get_session_async(run.session_id)
        prompt = _metadata_required_str(run.metadata, "prompt")
        return ExternalWorkerClaim(
            run=run,
            claim_token=claim_token,
            prompt=prompt,
            session=session,
        )

    async def claim_external_worker_run(
        self,
        *,
        worker_id: str,
        executor_kind: str,
        session_id: str | None = None,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> ExternalWorkerClaim | None:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.claim_attached_executor_run(
            executor_id=worker_id,
            executor_kind=executor_kind,
            session_id=session_id,
            lease_seconds=lease_seconds,
            worker_instance_id=worker_instance_id,
            process_id=process_id,
            capabilities=capabilities,
            workspace_sync=workspace_sync,
        )

    async def heartbeat_attached_executor_run(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> AgentRunRecord:
        run = await self._load_and_authorize_attached_executor_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
        )
        metadata = dict(run.metadata)
        metadata["lease_expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=lease_seconds)
        ).isoformat()
        metadata["last_heartbeat_at"] = datetime.now(UTC).isoformat()
        if worker_instance_id is not None:
            metadata["worker_instance_id"] = worker_instance_id
        if process_id is not None:
            metadata["process_id"] = process_id
        if capabilities is not None:
            metadata["capabilities"] = capabilities
        if workspace_sync is not None:
            metadata["workspace_sync"] = workspace_sync
        status = "running" if run.status == "claimed" else run.status
        return await self._require_runtime_store().update_agent_run(
            run_id,
            status=status,
            ended_at=run.ended_at,
            metadata=cast(JSONObject, metadata),
            result=run.result,
            error=run.error,
        )

    async def heartbeat_external_worker_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        claim_token: str,
        lease_seconds: int = 30,
        worker_instance_id: str | None = None,
        process_id: int | None = None,
        capabilities: JSONObject | None = None,
        workspace_sync: JSONObject | None = None,
    ) -> AgentRunRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.heartbeat_attached_executor_run(
            run_id=run_id,
            executor_id=worker_id,
            claim_token=claim_token,
            lease_seconds=lease_seconds,
            worker_instance_id=worker_instance_id,
            process_id=process_id,
            capabilities=capabilities,
            workspace_sync=workspace_sync,
        )

    async def append_attached_executor_event(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
        event_id: str,
        event_kind: str,
        payload: JSONObject,
        created_at: datetime,
    ) -> RuntimeEventRecord:
        run = await self._load_and_authorize_attached_executor_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
        )
        return await self._require_runtime_store().append_runtime_event(
            RuntimeEventRecord(
                event_id=event_id,
                run_id=run_id,
                event_kind=event_kind,
                payload=_with_runtime_event_correlation(
                    payload,
                    _runtime_event_correlation_from_run(run),
                ),
                created_at=created_at,
            )
        )

    async def append_external_worker_event(
        self,
        *,
        run_id: str,
        worker_id: str,
        claim_token: str,
        event_id: str,
        event_kind: str,
        payload: JSONObject,
        created_at: datetime,
    ) -> RuntimeEventRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.append_attached_executor_event(
            run_id=run_id,
            executor_id=worker_id,
            claim_token=claim_token,
            event_id=event_id,
            event_kind=event_kind,
            payload=payload,
            created_at=created_at,
        )

    async def finalize_attached_executor_run(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
        status: str,
        result: JSONObject,
        error: str | None,
        tape_id: str | None,
        tape_entries: list[JSONObject] | None = None,
    ) -> AgentRunRecord:
        run = await self._load_and_authorize_attached_executor_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
        )
        if status not in {"completed", "cancelled", "failed"}:
            raise ValueError("attached executor final status is invalid")
        metadata = dict(run.metadata)
        metadata["finalized_at"] = datetime.now(UTC).isoformat()
        if tape_id is not None:
            metadata["final_tape_id"] = tape_id
        if tape_id is not None and tape_entries is not None:
            await self._tape_store.save(tape_id, tape_entries)
        updated = await self._require_runtime_store().update_agent_run(
            run_id,
            status=status,
            ended_at=datetime.now(UTC),
            metadata=cast(JSONObject, metadata),
            result=result,
            error=error,
        )
        async with self._lock:
            session = await self.get_session_async(run.session_id)
            if tape_id is not None:
                session.tape_id = tape_id
            session.turn_in_progress = False
            session.turn_status = (
                cast(TurnStatus, status)
                if status in {"cancelled", "failed"}
                else "idle"
            )
            session.current_turn_id = run_id
            session.last_activity = datetime.now(UTC)
            session.last_failure_details = error if status == "failed" else None
            await self._persist_session_async(session)
        return updated

    async def finalize_external_worker_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        claim_token: str,
        status: str,
        result: JSONObject,
        error: str | None,
        tape_id: str | None,
        tape_entries: list[JSONObject] | None = None,
    ) -> AgentRunRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self.finalize_attached_executor_run(
            run_id=run_id,
            executor_id=worker_id,
            claim_token=claim_token,
            status=status,
            result=result,
            error=error,
            tape_id=tape_id,
            tape_entries=tape_entries,
        )

    async def _load_and_authorize_attached_executor_run(
        self,
        *,
        run_id: str,
        executor_id: str,
        claim_token: str,
    ) -> AgentRunRecord:
        run = await self.load_runtime_run(run_id)
        metadata = run.metadata
        if (
            metadata.get("execution_binding_kind")
            not in _ATTACHED_EXECUTOR_BINDING_KINDS
        ):
            raise ValueError("runtime run is not attached executor owned")
        owner_id = metadata.get("executor_id") or metadata.get("worker_id")
        if owner_id != executor_id:
            raise PermissionError("attached executor does not own this run")
        token_hash = metadata.get("claim_token_hash")
        if not isinstance(token_hash, str) or not secrets.compare_digest(
            token_hash,
            _hash_claim_token(claim_token),
        ):
            raise PermissionError("attached executor claim token is invalid")
        if run.status not in {"claimed", "running", "cancelling"}:
            raise PermissionError("attached executor claim is expired or inactive")
        lease_expires_at = _optional_metadata_datetime(metadata, "lease_expires_at")
        if lease_expires_at is None or lease_expires_at <= datetime.now(UTC):
            raise PermissionError("attached executor claim is expired or inactive")
        return run

    async def _load_and_authorize_external_worker_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        claim_token: str,
    ) -> AgentRunRecord:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self._load_and_authorize_attached_executor_run(
            run_id=run_id,
            executor_id=worker_id,
            claim_token=claim_token,
        )

    async def list_workspace_records(self) -> list[WorkspaceRecord]:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        return await self._workspace_metadata_store.list()

    async def load_workspace_record_by_workspace_id(
        self, workspace_id: str
    ) -> WorkspaceRecord | None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        return await self._workspace_metadata_store.load_by_workspace_id(workspace_id)

    async def update_workspace_record_status(
        self,
        workspace_record_id: str,
        *,
        status: WorkspaceStatus,
        cleanup_error: str | None = None,
    ) -> None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await self._workspace_metadata_store.update_status(
            workspace_record_id,
            status=status,
            cleanup_error=cleanup_error,
        )

    async def update_workspace_record_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: WorkspaceRetentionPolicy,
        expires_at: datetime | None,
        status: WorkspaceStatus,
    ) -> None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await self._workspace_metadata_store.update_retention(
            workspace_record_id,
            retention_policy=retention_policy,
            expires_at=expires_at,
            status=status,
        )

    async def update_workspace_record_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, JSONValue],
    ) -> None:
        if self._workspace_metadata_store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await self._workspace_metadata_store.update_result_refs(
            workspace_record_id,
            result_refs=result_refs,
        )

    def _get_pg_pool(self) -> PGPool:
        if self._pg_pool is not None:
            return cast(PGPool, self._pg_pool)

        pg_pool_type, _, _ = _load_pg_storage_types()

        dsn_obj = self._storage_config.get("dsn")
        if not isinstance(dsn_obj, str) or not dsn_obj.strip():
            raise RuntimeError("PG storage requires storage_config['dsn']")
        dsn = dsn_obj.strip()
        self._pg_pool = pg_pool_type(dsn=dsn)
        self._owns_pg_pool = True
        return cast(PGPool, self._pg_pool)

    def _create_http_session_store(self) -> SessionStore:
        configured_backend = self._storage_config.get("http_session_backend")
        tape_backend = (
            str(self._storage_config.get("tape_backend", "jsonl")).strip().lower()
        )
        if configured_backend is None:
            legacy_backend = self._storage_config.get("session_backend")
            if (
                isinstance(legacy_backend, str)
                and legacy_backend.strip().lower() == "pg"
            ):
                configured_backend = "pg"
            elif tape_backend == "pg":
                configured_backend = "pg"

        backend = (
            configured_backend.strip().lower()
            if isinstance(configured_backend, str)
            else None
        )
        dsn = self._storage_config.get("dsn")
        session_path = self._storage_config.get("http_session_path")
        return create_session_store(
            backend=backend,
            dsn=dsn if isinstance(dsn, str) else None,
            pg_pool=None,
            file_path=session_path if isinstance(session_path, str) else None,
        )

    def _create_tape_store(self, data_dir: Path) -> TapeStore:
        backend = str(self._storage_config.get("tape_backend", "jsonl")).strip().lower()
        if backend == "pg":
            _, PGTapeStore, _ = _load_pg_storage_types()
            return cast(TapeStore, PGTapeStore(pool=self._get_pg_pool()))
        if backend == "sqlite":
            path_obj = self._storage_config.get("tape_path")
            path = (
                Path(path_obj)
                if isinstance(path_obj, str) and path_obj.strip()
                else data_dir / "tape.sqlite3"
            )
            return SQLiteTapeStore(path)
        return JSONLTapeStore(data_dir / "tapes")

    def _create_checkpoint_store(self, data_dir: Path) -> CheckpointStore:
        tape_backend = (
            str(self._storage_config.get("tape_backend", "jsonl")).strip().lower()
        )
        default_backend = "pg" if tape_backend == "pg" else "fs"
        backend = (
            str(self._storage_config.get("checkpoint_backend", default_backend))
            .strip()
            .lower()
        )
        if backend == "pg":
            _, _, PGCheckpointStore = _load_pg_storage_types()
            return cast(CheckpointStore, PGCheckpointStore(pool=self._get_pg_pool()))
        if backend == "sqlite":
            path_obj = self._storage_config.get("checkpoint_path")
            path = (
                Path(path_obj)
                if isinstance(path_obj, str) and path_obj.strip()
                else data_dir / "checkpoints.sqlite3"
            )
            return SQLiteCheckpointStore(path)
        return FSCheckpointStore(data_dir / "checkpoints")

    def _create_runtime_store(self) -> RuntimeStoreProtocol | None:
        configured_backend = self._storage_config.get("runtime_backend")
        if configured_backend is None:
            return None
        if not isinstance(configured_backend, str):
            raise ValueError("storage.runtime_backend must be a string")
        backend = configured_backend.strip().lower()
        if backend in {"", "none", "disabled"}:
            return None
        if backend == "pg":
            return PGRuntimeStore(pool=self._get_pg_pool())
        if backend in {"jsonl", "fs", "file"}:
            path_obj = self._storage_config.get("runtime_path")
            root = (
                Path(path_obj)
                if isinstance(path_obj, str) and path_obj.strip()
                else Path(os.environ.get("AGENT_DATA_DIR", "./data")) / "runtime"
            )
            return JSONLRuntimeStore(root)
        if backend == "sqlite":
            path_obj = self._storage_config.get("runtime_path")
            path = (
                Path(path_obj)
                if isinstance(path_obj, str) and path_obj.strip()
                else Path(os.environ.get("AGENT_DATA_DIR", "./data"))
                / "runtime.sqlite3"
            )
            return SQLiteRuntimeStore(path)
        raise ValueError(f"unsupported storage.runtime_backend: {backend}")

    async def _close_runtime(self, session: Session) -> None:
        adapter = session.runtime_adapter
        self._invalidate_runtime(session)
        await self._close_runtime_adapter(adapter)

    async def _close_runtime_adapter(self, adapter: object | None) -> None:
        if adapter is None:
            return
        close = getattr(adapter, "close", None)
        if callable(close):
            close_result = close()
            if isawaitable(close_result):
                await close_result

    def _close_runtime_sync_safe(self, session: Session) -> None:
        adapter = session.runtime_adapter
        self._invalidate_runtime(session)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._close_runtime_adapter(adapter))
            return
        _ = loop.create_task(self._close_runtime_adapter(adapter))

    def _session_uses_provisioned_cloud_workspace(self, session: Session) -> bool:
        origin = session.origin
        return (
            isinstance(session.execution_binding, CloudWorkspaceBinding)
            and origin is not None
            and origin.get("binding_kind") == "cloud"
            and origin.get("workspace_source_kind") is not None
        )

    def _cleanup_provisioned_cloud_binding(self, session: Session) -> None:
        if self._provisioned_cloud_binding_cleanup is None:
            return
        if not self._session_uses_provisioned_cloud_workspace(session):
            return
        binding = session.execution_binding
        if not isinstance(binding, CloudWorkspaceBinding):
            return
        self._provisioned_cloud_binding_cleanup(binding)

    async def _cleanup_provisioned_cloud_binding_async(
        self, session: Session
    ) -> str | None:
        try:
            await asyncio.to_thread(self._cleanup_provisioned_cloud_binding, session)
            return None
        except Exception as exc:
            logger.exception(
                "Failed to clean up provisioned cloud workspace for session %s",
                session.id,
            )
            return str(exc) or "provisioned cloud workspace cleanup failed"

    async def _workspace_record_for_session(
        self, session: Session
    ) -> WorkspaceRecord | None:
        if self._workspace_metadata_store is None:
            return None
        binding = session.execution_binding
        if not isinstance(binding, CloudWorkspaceBinding):
            return None
        return await self._workspace_metadata_store.load_for_session_workspace(
            session_id=session.id,
            workspace_id=binding.workspace_id,
        )

    async def _finalize_provisioned_cloud_workspace_on_close(
        self, session: Session
    ) -> None:
        if not self._session_uses_provisioned_cloud_workspace(session):
            return

        record = await self._workspace_record_for_session(session)
        store = self._workspace_metadata_store
        if record is not None and record.retention_policy != "delete_on_close":
            if store is None:
                raise RuntimeError("workspace metadata store is not configured")
            await store.update_status(
                record.workspace_record_id,
                status="retained",
                cleanup_error=None,
            )
            return

        cleanup_error = await self._cleanup_provisioned_cloud_binding_async(session)
        if record is None:
            return
        if store is None:
            raise RuntimeError("workspace metadata store is not configured")
        await store.update_status(
            record.workspace_record_id,
            status="cleanup_failed" if cleanup_error is not None else "cleaned",
            cleanup_error=cleanup_error,
        )

    def _create_agent_for_session(self, **kwargs: Any) -> tuple[Any, Any]:
        factory = self._create_agent
        if factory is None:
            factory = importlib.import_module("coding_agent.__main__").create_agent
        return factory(**kwargs)

    def _bind_subagent_message_publisher(self, ctx: Any) -> None:
        ctx.config["subagent_message_publisher"] = self.publish_subagent_message

    def _bind_root_run_identity(
        self,
        session: Session,
        ctx: Any,
        run_id: str,
        *,
        resume_context: SessionResumeContext | None = None,
    ) -> None:
        if hasattr(ctx, "session_id"):
            ctx.session_id = session.id
        run_context = getattr(ctx, "run_context", None)
        if run_context is not None:
            if not isinstance(run_context, AgentRunContext):
                raise TypeError("runtime context run_context must be AgentRunContext")
            trace_metadata = dict(run_context.trace_metadata)
            trace_metadata["turn_id"] = run_id
            trace_metadata["tape_id"] = ctx.tape.tape_id
            trace_metadata["execution_placement"] = self._execution_placement(session)
            trace_metadata["execution_binding_kind"] = session.execution_binding.kind
            trace_metadata["workspace_surface"] = (
                session.execution_binding.workspace_surface
            )
            trace_metadata["execution_plane"] = (
                session.execution_binding.execution_plane
            )
            if resume_context is not None:
                trace_metadata.update(resume_context.metadata())
            ctx.run_context = replace(
                run_context,
                session_id=session.id,
                run_id=run_id,
                parent_run_id=(
                    None if resume_context is None else resume_context.previous_run_id
                ),
                trace_metadata=trace_metadata,
            )

    def _run_metadata_for_session(
        self,
        session: Session,
        *,
        resume_context: SessionResumeContext | None = None,
    ) -> JSONObject:
        metadata: JSONObject = {
            "provider_name": session.provider_name,
            "model_name": session.model_name,
            "approval_policy": session.approval_policy.value,
            "max_steps": session.max_steps,
            "execution_binding_kind": session.execution_binding.kind,
            "workspace_surface": session.execution_binding.workspace_surface,
            "execution_plane": session.execution_binding.execution_plane,
            "execution_placement": self._execution_placement(session),
        }
        if resume_context is not None:
            metadata.update(resume_context.metadata())
        if isinstance(session.execution_binding, ExternalWorkerBinding):
            metadata["executor_kind"] = session.execution_binding.executor_kind
            metadata["worker_pool"] = session.execution_binding.worker_pool
            if session.execution_binding.workspace_ref is not None:
                metadata["workspace_ref"] = cast(
                    RuntimeJSONValue,
                    dict(session.execution_binding.workspace_ref),
                )
        return metadata

    def _observation_attributes_for_session(
        self,
        session: Session,
        ctx: Any,
        *,
        resume_context: SessionResumeContext | None = None,
    ) -> JSONObject:
        attributes: JSONObject = {
            "tape_id": getattr(getattr(ctx, "tape", None), "tape_id", None),
            "execution_placement": self._execution_placement(session),
            "execution_binding_kind": session.execution_binding.kind,
            "workspace_surface": session.execution_binding.workspace_surface,
            "execution_plane": session.execution_binding.execution_plane,
        }
        if resume_context is not None:
            attributes.update(resume_context.metadata())
        return attributes

    def _execution_placement(self, session: Session) -> str:
        if isinstance(session.execution_binding, ExternalWorkerBinding):
            return "local_attached"
        if isinstance(session.execution_binding, CloudWorkspaceBinding):
            return "cloud_workspace"
        return "server_embedded"

    def _result_from_turn_outcome(self, outcome: TurnOutcome) -> JSONObject:
        return {
            "stop_reason": outcome.stop_reason.value,
            "steps_taken": outcome.steps_taken,
        }

    def _status_from_turn_outcome(self, outcome: TurnOutcome) -> str:
        if outcome.stop_reason == StopReason.INTERRUPTED:
            return "interrupted"
        if outcome.error is not None or outcome.stop_reason == StopReason.ERROR:
            return "failed"
        return "completed"

    def _agent_observation_status(self, turn_status: str) -> AgentObservationStatus:
        if turn_status in {"cancelled", "interrupted"}:
            return "cancelled"
        if turn_status == "failed":
            return "error"
        return "ok"

    def _latest_turn_trace(self, ctx: Any) -> TurnTrace | None:
        tape = getattr(ctx, "tape", None)
        if tape is None or not hasattr(tape, "snapshot"):
            return None
        turns = extract_turns(tape.snapshot())
        if not turns:
            return None
        return turns[-1]

    def _agent_observation_sink(self, ctx: Any) -> ObservationSink | None:
        config = getattr(ctx, "config", None)
        if not isinstance(config, dict):
            return None
        sink = config.get("observation_sink")
        if isinstance(sink, ObservationSink):
            return sink
        return None

    def _start_agent_observation(
        self,
        *,
        session: Session,
        ctx: Any,
        run_id: str,
        prompt: str,
        resume_context: SessionResumeContext | None = None,
    ) -> AgentObservationRecorder | None:
        config = getattr(ctx, "config", None)
        if not isinstance(config, dict):
            return None
        recorder = AgentObservationRecorder(
            store=self._agent_observation_store,
            sink=self._agent_observation_sink(ctx),
        )
        config["agent_observation_recorder"] = recorder
        recorder.start_turn(
            session_id=session.id,
            run_id=run_id,
            prompt=prompt,
            attributes=self._observation_attributes_for_session(
                session,
                ctx,
                resume_context=resume_context,
            ),
        )
        return recorder

    def _complete_agent_observation(
        self,
        recorder: AgentObservationRecorder | None,
        *,
        ctx: Any,
        turn_status: str,
    ) -> None:
        if recorder is None:
            return
        recorder.complete_turn(
            status=self._agent_observation_status(turn_status),
            turn=self._latest_turn_trace(ctx),
        )

    def _require_turn_outcome(self, outcome: object) -> TurnOutcome:
        if not isinstance(outcome, TurnOutcome):
            raise TypeError("runtime store requires PipelineAdapter.run_turn outcome")
        return outcome

    async def _create_runtime_agent_run(
        self,
        session: Session,
        *,
        run_id: str,
        started_at: datetime,
        resume_context: SessionResumeContext | None = None,
    ) -> bool:
        if self._runtime_store is None:
            return False
        await self._runtime_store.create_agent_run(
            AgentRunRecord(
                run_id=run_id,
                session_id=session.id,
                tape_id=session.tape_id,
                parent_run_id=(
                    None if resume_context is None else resume_context.previous_run_id
                ),
                agent_id=None,
                status="queued",
                started_at=started_at,
                metadata=self._run_metadata_for_session(
                    session,
                    resume_context=resume_context,
                ),
                result={},
                error=None,
            )
        )
        return True

    async def _update_runtime_agent_run(
        self,
        session: Session,
        *,
        run_id: str,
        status: str,
        ended_at: datetime | None,
        result: JSONObject,
        error: str | None,
        resume_context: SessionResumeContext | None = None,
    ) -> None:
        if self._runtime_store is None:
            return
        await self._runtime_store.update_agent_run(
            run_id,
            status=status,
            ended_at=ended_at,
            metadata=self._run_metadata_for_session(
                session,
                resume_context=resume_context,
            ),
            result=result,
            error=error,
        )

    async def _finish_runtime_agent_run(
        self,
        session: Session,
        *,
        run_id: str,
        status: str,
        result: JSONObject,
        error: str | None,
        resume_context: SessionResumeContext | None = None,
    ) -> None:
        await self._update_runtime_agent_run(
            session,
            run_id=run_id,
            status=status,
            ended_at=datetime.now(UTC),
            result=result,
            error=error,
            resume_context=resume_context,
        )

    async def recover_stale_runtime_runs(
        self,
        *,
        recovered_at: datetime | None = None,
    ) -> int:
        if self._runtime_store is None:
            return 0
        recovery_time = recovered_at or datetime.now(UTC)
        recovered_count = 0
        for session_id in await self.list_sessions_async():
            if self._owner_store is not None and not (
                await self._holds_active_owner_lease(session_id)
            ):
                continue
            runs = await self._runtime_store.list_agent_runs(session_id)
            for run in runs:
                if await self._recover_expired_attached_executor_run(
                    run,
                    recovered_at=recovery_time,
                ):
                    recovered_count += 1
                    continue
                if run.status != "running" or run.ended_at is not None:
                    continue
                metadata = dict(run.metadata)
                metadata["reclaimable"] = True
                metadata["recovered_at"] = recovery_time.isoformat()
                metadata["recovery_reason"] = _STALE_RUNTIME_RUN_RECOVERY_REASON
                if self._owner_id is not None:
                    metadata["recovered_by_owner_id"] = self._owner_id
                await self._runtime_store.update_agent_run(
                    run.run_id,
                    status="interrupted",
                    ended_at=recovery_time,
                    metadata=metadata,
                    result=run.result,
                    error=_STALE_RUNTIME_RUN_ERROR,
                )
                recovered_count += 1
        return recovered_count

    async def _recover_expired_attached_executor_run(
        self,
        run: AgentRunRecord,
        *,
        recovered_at: datetime,
    ) -> bool:
        if self._runtime_store is None:
            return False
        if (
            run.metadata.get("execution_binding_kind")
            not in _ATTACHED_EXECUTOR_BINDING_KINDS
        ):
            return False
        if run.status not in {"claimed", "running", "cancelling"}:
            return False
        lease_expires_at = _optional_metadata_datetime(
            run.metadata,
            "lease_expires_at",
        )
        if lease_expires_at is None or lease_expires_at > recovered_at:
            return False
        metadata = dict(run.metadata)
        metadata["reclaimable"] = True
        metadata["recovered_at"] = recovered_at.isoformat()
        metadata["recovery_reason"] = "attached_executor_lease_expired"
        metadata["legacy_recovery_reason"] = "external_worker_lease_expired"
        metadata["previous_status"] = run.status
        if self._owner_id is not None:
            metadata["recovered_by_owner_id"] = self._owner_id
        await self._runtime_store.update_agent_run(
            run.run_id,
            status="expired",
            ended_at=None,
            metadata=cast(JSONObject, metadata),
            result=run.result,
            error="external worker lease expired",
        )
        return True

    async def _recover_expired_external_worker_run(
        self,
        run: AgentRunRecord,
        *,
        recovered_at: datetime,
    ) -> bool:
        """Compatibility wrapper for the legacy external-worker API."""
        return await self._recover_expired_attached_executor_run(
            run,
            recovered_at=recovered_at,
        )

    async def _append_runtime_wire_event(
        self,
        session: Session,
        message: WireMessage,
    ) -> None:
        if self._runtime_store is None:
            return
        run_id = session.current_turn_id
        if run_id is None:
            return
        run = await self._runtime_store.load_agent_run(run_id)
        if run is None:
            correlation: JSONObject = {
                "session_id": session.id,
                "run_id": run_id,
                "execution_placement": self._execution_placement(session),
                "execution_binding_kind": session.execution_binding.kind,
                "workspace_surface": session.execution_binding.workspace_surface,
                "execution_plane": session.execution_binding.execution_plane,
            }
            if session.tape_id is not None:
                correlation["tape_id"] = session.tape_id
        else:
            correlation = _runtime_event_correlation_from_run(run)
        await self._runtime_store.append_runtime_event(
            RuntimeEventRecord(
                event_id=f"{run_id}:wire:{uuid.uuid4().hex}",
                run_id=run_id,
                event_kind=f"wire.{type(message).__name__}",
                payload=_with_runtime_event_correlation(
                    _wire_message_event_payload(message),
                    correlation,
                ),
                created_at=message.timestamp,
            )
        )

    async def _save_runtime_message_snapshot(
        self,
        session: Session,
        ctx: Any,
        *,
        run_id: str,
    ) -> None:
        if self._runtime_store is None:
            return
        messages = _runtime_message_snapshot(getattr(ctx, "messages", None))
        if messages is None:
            return
        await self._runtime_store.save_message_snapshot(
            RunMessageSnapshotRecord(
                snapshot_id=f"{run_id}:latest",
                run_id=run_id,
                messages=messages,
                metadata={
                    "session_id": session.id,
                    "tape_id": session.tape_id,
                    "message_count": len(messages),
                    "snapshot_kind": "latest_context",
                },
                created_at=datetime.now(UTC),
            )
        )

    def _approval_interaction_metadata(
        self,
        session: Session,
        request: ApprovalRequest,
    ) -> JSONObject:
        metadata: JSONObject = {
            "session_id": session.id,
            "request_id": request.request_id,
        }
        if request.tool_call is not None:
            metadata["tool_call_id"] = request.tool_call.call_id
            metadata["tool_name"] = request.tool_call.tool_name
        if self._owner_id is not None:
            metadata["owner_id"] = self._owner_id
        if self._fencing_token is not None:
            metadata["fencing_token"] = self._fencing_token
        return metadata

    async def _create_approval_interaction(
        self,
        session: Session,
        request: ApprovalRequest,
    ) -> str | None:
        if self._runtime_store is None:
            return None
        run_id = session.current_turn_id
        if run_id is None:
            return None
        interaction_id = _approval_interaction_id(run_id, request.request_id)
        await self._runtime_store.create_agent_interaction(
            AgentInteractionRecord(
                interaction_id=interaction_id,
                run_id=run_id,
                interaction_kind="approval",
                status="pending",
                request_payload=_approval_request_payload(request),
                response_payload={},
                metadata=self._approval_interaction_metadata(session, request),
                created_at=request.timestamp,
            )
        )
        return interaction_id

    async def _resolve_approval_interaction(
        self,
        session: Session,
        request_id: str,
        response: ApprovalResponse,
        *,
        status: str | None = None,
    ) -> None:
        if self._runtime_store is None:
            return
        run_id = session.current_turn_id
        if run_id is None:
            return
        await self._runtime_store.resolve_agent_interaction(
            _approval_interaction_id(run_id, request_id),
            status=status or _approval_interaction_status(response),
            response_payload=_approval_response_payload(response),
            resolved_at=response.timestamp,
        )

    async def _send_session_wire_message(
        self,
        session: Session,
        message: WireMessage,
    ) -> None:
        await self._append_runtime_wire_event(session, message)
        await session.wire.send(message)

    def _turn_lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._session_turn_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_turn_locks[session_id] = lock
        return lock

    def _workspace_export_in_progress(self, session_id: str) -> bool:
        return self._session_workspace_export_counts.get(session_id, 0) > 0

    def _begin_workspace_export(self, session_id: str) -> None:
        self._session_workspace_export_counts[session_id] = (
            self._session_workspace_export_counts.get(session_id, 0) + 1
        )

    def _end_workspace_export(self, session_id: str) -> None:
        count = self._session_workspace_export_counts.get(session_id, 0)
        if count <= 1:
            self._session_workspace_export_counts.pop(session_id, None)
            return
        self._session_workspace_export_counts[session_id] = count - 1

    async def prepare_session_turn(self, session_id: str) -> Session:
        turn_lock = self._turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")
        if self._workspace_export_in_progress(session_id):
            raise RuntimeError("turn already in progress")

        session = await self.get_session_async(session_id)
        await self._assert_owner(session_id)
        if session.turn_in_progress or (session.task and not session.task.done()):
            raise RuntimeError("turn already in progress")
        return session

    async def _assert_owner(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            raise SessionOwnershipConflictError(
                "session has no owner",
                reason=SessionOwnershipConflictReason.MISSING_OWNER,
            )
        if owner.lease_expires_at <= datetime.now(UTC):
            raise SessionOwnershipConflictError(
                "session owner lease expired",
                reason=SessionOwnershipConflictReason.EXPIRED_LEASE,
            )

        current_owner_id = owner.owner_id
        current_fencing_token = owner.fencing_token

        if (
            current_owner_id != self._owner_id
            or current_fencing_token != self._fencing_token
        ):
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    async def authorize_event_stream(self, session_id: str) -> None:
        await self._assert_owner(session_id)

    async def verify_event_stream_ownership(self, session_id: str) -> None:
        await self._assert_owner(session_id)

    async def _run_store_io(self, func: Callable[..., T], /, *args: object) -> T:
        def run_guarded() -> T:
            with self._store_io_guard:
                return func(*args)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, run_guarded)

    async def _persist_session_async(self, session: Session) -> None:
        self._session_cache[session.id] = session
        await self._run_store_io(
            self._store.save,
            session.id,
            cast(dict[str, Any], session.to_store_data()),
        )

    async def _persist_workspace_record_for_session(self, session: Session) -> None:
        store = self._workspace_metadata_store
        if store is None:
            return
        binding = session.execution_binding
        if not isinstance(binding, CloudWorkspaceBinding):
            return
        origin = session.origin or {}
        if (
            origin.get("binding_kind") != "cloud"
            or origin.get("workspace_source_kind") is None
        ):
            return

        provider = origin.get("workspace_provider") or "docker"
        provider_instance_id = origin.get("provider_instance_id")
        workspace_root_ref = origin.get("workspace_root_ref")
        workspace_host_label = origin.get("workspace_host_label")
        owner_label = origin.get("owner_label")
        source_kind = origin.get("workspace_source_kind")
        if (
            not isinstance(provider, str)
            or not isinstance(provider_instance_id, str)
            or not isinstance(workspace_root_ref, str)
            or not isinstance(workspace_host_label, str)
            or not isinstance(owner_label, str)
            or not isinstance(source_kind, str)
        ):
            raise RuntimeError(
                "cloud workspace session is missing durable workspace metadata"
            )

        source_ref: dict[str, JSONValue] = {}
        if binding.runtime_profile is not None:
            source_ref["runtime_profile"] = binding.runtime_profile
        await store.save(
            WorkspaceRecord(
                workspace_record_id=f"{session.id}:{binding.workspace_id}",
                workspace_id=binding.workspace_id,
                session_id=session.id,
                provider=provider,
                provider_instance_id=provider_instance_id,
                workspace_root_ref=workspace_root_ref,
                workspace_host_label=workspace_host_label,
                owner_label=owner_label,
                source_kind=source_kind,
                source_ref=source_ref,
                status="active",
                retention_policy="delete_on_close",
            )
        )

    async def _acquire_owner_for_session(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        acquired = await self._owner_store.acquire(
            session_id,
            self._owner_id,
            lease_seconds=self._owner_lease_seconds,
            fencing_token=self._fencing_token,
        )
        if not acquired:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    async def _holds_owner_lease(self, session_id: str) -> bool:
        if self._owner_store is None:
            return False
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            return False
        return (
            owner.owner_id == self._owner_id
            and owner.fencing_token == self._fencing_token
        )

    async def _holds_active_owner_lease(self, session_id: str) -> bool:
        if self._owner_store is None:
            return False
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            return False
        return (
            owner.owner_id == self._owner_id
            and owner.fencing_token == self._fencing_token
            and owner.lease_expires_at > datetime.now(UTC)
        )

    async def release_owned_sessions(self) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        for session_id in await self.list_sessions_async():
            await self._release_owner_lease_for_session(session_id)

    async def _release_owner_lease_for_session(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        if not await self._holds_owner_lease(session_id):
            return
        try:
            released = await self._owner_store.release(
                session_id,
                self._owner_id,
                self._fencing_token,
            )
        except Exception:
            logger.warning(
                "Failed to release owner lease for session %s owned by %s with fencing token %s",
                session_id,
                self._owner_id,
                self._fencing_token,
                exc_info=True,
            )
            return
        if not released:
            logger.warning(
                "Failed to release owner lease for session %s owned by %s with fencing token %s",
                session_id,
                self._owner_id,
                self._fencing_token,
            )

    async def renew_owner_leases(self) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        now = datetime.now(UTC)
        for session_id in await self.list_sessions_async():
            owner = await self._owner_store.get_owner(session_id)
            if owner is None:
                continue
            if (
                owner.owner_id != self._owner_id
                or owner.fencing_token != self._fencing_token
                or owner.lease_expires_at <= now
            ):
                continue
            try:
                renewed = await self._owner_store.renew(
                    session_id,
                    self._owner_id,
                    lease_seconds=self._owner_lease_seconds,
                    new_fencing_token=self._fencing_token,
                    current_fencing_token=self._fencing_token,
                )
            except Exception:
                logger.warning(
                    "Failed to renew owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    self._fencing_token,
                    exc_info=True,
                )
                continue
            if not renewed:
                logger.warning(
                    "Failed to renew owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    self._fencing_token,
                )

    async def backfill_owner_leases(self) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        now = datetime.now(UTC)
        for session_id in await self.list_sessions_async():
            try:
                owner = await self._owner_store.get_owner(session_id)
                if owner is not None and owner.lease_expires_at > now:
                    continue
                acquired = await self._owner_store.acquire(
                    session_id,
                    self._owner_id,
                    lease_seconds=self._owner_lease_seconds,
                    fencing_token=self._fencing_token,
                )
            except Exception:
                logger.warning(
                    "Failed to backfill owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    self._fencing_token,
                    exc_info=True,
                )
                continue
            if not acquired:
                logger.warning(
                    "Failed to backfill owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    self._fencing_token,
                )

    async def get_session_async(self, session_id: str) -> Session:
        session = self._session_cache.get(session_id)
        if session is not None:
            return session
        loaded = await self._run_store_io(self._store.load, session_id)
        if loaded is None:
            raise KeyError(f"Session not found: {session_id}")
        return self._hydrate_session(
            Session.from_store_data(cast(dict[str, Any], loaded))
        )

    async def has_session_async(self, session_id: str) -> bool:
        if session_id in self._session_cache:
            return True
        return await self._run_store_io(self._store.load, session_id) is not None

    async def has_event_queue_async(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> bool:
        session = await self.get_session_async(session_id)
        return queue in session.event_queues

    async def list_sessions_async(self) -> list[str]:
        return await self._run_store_io(self._store.list_sessions)

    async def count_sessions_async(self) -> int:
        return await self._run_store_io(self._store.count_sessions)

    async def get_session_info_async(self, session_id: str) -> dict[str, Any]:
        session = await self.get_session_async(session_id)
        return session.as_dict()

    async def add_event_queue_async(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = await self.get_session_async(session_id)
        session.event_queues.append(queue)

    async def register_owned_event_queue_async(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            session.event_queues.append(queue)
            try:
                await self._assert_owner(session_id)
            except (Exception, asyncio.CancelledError):
                if queue in session.event_queues:
                    session.event_queues.remove(queue)
                raise

    async def remove_event_queue_async(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = await self.get_session_async(session_id)
        if queue in session.event_queues:
            session.event_queues.remove(queue)

    async def check_health_async(self) -> bool:
        return bool(await self._run_store_io(self._store.check_health))

    async def _close_resource_async(self, resource: object) -> None:
        close = getattr(resource, "close", None)
        if not callable(close):
            return
        close_result = await self._run_store_io(close)
        if isawaitable(close_result):
            await close_result

    async def _remove_session_async_no_lock(self, session_id: str) -> None:
        session = await self.get_session_async(session_id)
        await self._close_runtime(session)
        await self._finalize_provisioned_cloud_workspace_on_close(session)
        self._session_cache.pop(session_id, None)
        await self._run_store_io(self._store.delete, session_id)
        await self._release_owner_lease_for_session(session_id)
        self._approval_stores.pop(session_id, None)
        self._session_turn_locks.pop(session_id, None)

    async def remove_session_async(self, session_id: str) -> None:
        async with self._lock:
            await self._remove_session_async_no_lock(session_id)

    async def _restore_tape(self, tape_id: str | None) -> Tape | None:
        if tape_id is None:
            return None
        entries = await self._tape_store.load(tape_id)
        if not entries:
            return Tape(tape_id=tape_id)
        return Tape.from_list(entries, tape_id=tape_id)

    def _make_restore_consumer(self, wire: LocalWire) -> _WireConsumer:
        async def _reject_approval(req: ApprovalRequest) -> ApprovalResponse:
            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=False,
                feedback="Checkpoint restore does not support approval prompts",
            )

        return _WireConsumer(wire, _reject_approval)

    def _make_session_consumer(self, session: Session) -> _WireConsumer:
        async def _request_approval(req: ApprovalRequest) -> ApprovalResponse:
            if session.approval_coordinator.is_session_approved(req):
                response = ApprovalResponse(
                    session_id=req.session_id,
                    request_id=req.request_id,
                    approved=True,
                    scope="session",
                )
                await self._create_approval_interaction(session, req)
                await self._resolve_approval_interaction(
                    session,
                    req.request_id,
                    response,
                )
                return response
            session.approval_coordinator.add_request(req)
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_event.clear()
            session.approval_response = None
            await self._persist_session_async(session)
            await self._create_approval_interaction(session, req)
            published_decision = await self._published_approval_decision(
                session,
                req.request_id,
            )
            if published_decision is not None:
                response = await self._apply_published_approval_decision(
                    session,
                    req.request_id,
                    published_decision,
                )
                if response is not None:
                    return response
            await self._send_session_wire_message(session, req)
            try:
                response = await session.approval_coordinator.wait_for_response(
                    req.request_id,
                    float(req.timeout_seconds),
                )
                if response is None:
                    timeout_response = ApprovalResponse(
                        session_id=req.session_id,
                        request_id=req.request_id,
                        approved=False,
                        feedback="Approval timeout or error",
                    )
                    await self._resolve_approval_interaction(
                        session,
                        req.request_id,
                        timeout_response,
                        status="timed_out",
                    )
                    return timeout_response

                session.approval_response = {
                    "decision": "approve" if response.approved else "deny",
                    "feedback": response.feedback,
                }
                session.approval_event.set()
                session.pending_approval = session.approval_coordinator.projection()
                await self._persist_session_async(session)
                await self._resolve_approval_interaction(
                    session,
                    req.request_id,
                    response,
                )
                return response
            finally:
                session.pending_approval = session.approval_coordinator.projection()
                session.approval_response = None
                await self._persist_session_async(session)

        return _WireConsumer(
            session.wire,
            _request_approval,
            emit_handler=lambda message: self._send_session_wire_message(
                session,
                message,
            ),
        )

    async def _restore_checkpoint(self, session: Session, checkpoint_id: str) -> None:
        snapshot = await self._checkpoint_service.restore(checkpoint_id)
        meta = snapshot.meta
        if session.tape_id is None:
            raise ValueError("session has no stable tape id")
        if meta.tape_id != session.tape_id:
            raise ValueError(
                f"Checkpoint {checkpoint_id} belongs to tape {meta.tape_id}, not session tape {session.tape_id}"
            )
        if meta.entry_count != len(snapshot.tape_entries):
            raise ValueError(
                "checkpoint entry_count does not match snapshot tape_entries length"
            )
        if meta.window_start > meta.entry_count:
            raise ValueError("checkpoint window_start must be <= entry_count")

        restored_tape = Tape(
            entries=[Entry.from_dict(entry) for entry in snapshot.tape_entries],
            tape_id=session.tape_id,
            _window_start=meta.window_start,
        )

        restored_config = _checkpoint_session_config_from_extra(session, snapshot.extra)
        previous_provider_name = session.provider_name
        previous_model_name = session.model_name
        previous_base_url = session.base_url
        environment = self._resolve_environment_for_run_target(session.default_run_target)
        workspace_root = self._environment_workspace_root(environment)

        approval_mode_map = {
            ApprovalPolicy.YOLO: "yolo",
            ApprovalPolicy.INTERACTIVE: "interactive",
            ApprovalPolicy.AUTO: "auto",
        }
        pipeline, ctx = self._create_agent_for_session(
            workspace_root=workspace_root,
            environment=environment,
            model_override=restored_config.model_name,
            provider_override=restored_config.provider_name,
            base_url_override=restored_config.base_url,
            max_steps_override=restored_config.max_steps,
            approval_mode_override=approval_mode_map[restored_config.approval_policy],
            session_id_override=session.id,
            api_key=None,
            tape=restored_tape,
        )
        ctx.config["wire_consumer"] = None
        ctx.config["agent_id"] = ""
        self._bind_subagent_message_publisher(ctx)

        provider_model_name = getattr(session.provider, "model_name", None)
        can_reuse_provider = (
            session.provider is not None
            and session.provider_name == restored_config.provider_name
            and provider_model_name == restored_config.model_name
            and previous_base_url == restored_config.base_url
        )
        if can_reuse_provider:
            llm_plugin = pipeline._registry.get("llm_provider")
            llm_plugin._instance = session.provider

        consumer = self._make_restore_consumer(session.wire)
        ctx.config["wire_consumer"] = consumer
        for key, value in snapshot.plugin_states.items():
            ctx.plugin_states.setdefault(key, value)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
        initialize = getattr(adapter, "initialize", None)
        if callable(initialize):
            initialize_result = initialize()
            if isawaitable(initialize_result):
                await initialize_result

        await self._close_runtime(session)
        await self._tape_store.truncate(session.tape_id, meta.entry_count)
        session.tape_id = ctx.tape.tape_id
        session.provider_name = restored_config.provider_name
        session.model_name = restored_config.model_name
        session.base_url = restored_config.base_url
        session.max_steps = restored_config.max_steps
        session.approval_policy = restored_config.approval_policy
        if (
            previous_provider_name != restored_config.provider_name
            or previous_model_name != restored_config.model_name
            or previous_base_url != restored_config.base_url
        ):
            session.provider = None
        session.runtime_pipeline = pipeline
        session.runtime_ctx = ctx
        session.runtime_adapter = adapter
        await self._persist_session_async(session)

        checkpoints = await self._checkpoint_service.list(ctx.tape.tape_id)
        for checkpoint_meta in checkpoints:
            if checkpoint_meta.entry_count > meta.entry_count:
                await self._checkpoint_service.delete(checkpoint_meta.checkpoint_id)

    def _persist_session(self, session: Session) -> None:
        self._session_cache[session.id] = session
        self._store.save(session.id, cast(dict[str, Any], session.to_store_data()))

    def _resolve_environment(self, session: Session) -> Environment:
        return self._binding_resolver.resolve_environment(session.execution_binding)

    def _resolve_environment_for_run_target(
        self,
        target: RunTarget | None,
    ) -> Environment:
        if target is None:
            raise RuntimeError("session is missing default_run_target")
        workspace = target.workspace
        if isinstance(workspace, LocalPathWorkspaceRef):
            return LocalEnvironment(Path(workspace.path).expanduser().resolve())
        if isinstance(workspace, CloudWorkspaceRef):
            return self._binding_resolver.resolve_environment(
                CloudWorkspaceBinding(
                    workspace_url=workspace.workspace_url,
                    workspace_id=workspace.workspace_id,
                    runtime_profile=workspace.runtime_profile,
                    workspace_provider=workspace.workspace_provider,
                    provider_instance_id=workspace.provider_instance_id,
                )
            )
        raise ValueError(
            f"runtime builders cannot resolve workspace target: {workspace.kind}"
        )

    def _resolve_local_daemon_environment(self, target: RunTarget) -> Environment:
        if not isinstance(target.executor, LocalDaemonExecutorRef):
            raise ValueError("local daemon runs require a local_daemon executor target")
        if not isinstance(target.workspace, LocalPathWorkspaceRef):
            raise ValueError("local daemon runs require a local_path workspace target")
        return self._resolve_environment_for_run_target(target)

    def _environment_workspace_root(self, environment: Environment) -> Path | None:
        local_root = environment.workspace_summary().local_root
        if local_root is None:
            return None
        return Path(local_root).expanduser().resolve()

    def _runtime_environment_workspace_root(self, ctx: object) -> Path | None:
        run_context = getattr(ctx, "run_context", None)
        environment = getattr(run_context, "environment", None)
        if environment is None:
            return None
        return self._environment_workspace_root(cast(Environment, environment))

    async def _submit_runtime_run_request(
        self,
        session: Session,
        *,
        run_id: str,
        prompt: str,
        resume_context: SessionResumeContext | None = None,
    ) -> RunRequest:
        input_summary = prompt if prompt.strip() else None
        request = RunRequest(
            session_id=session.id,
            run_id=run_id,
            target=session.default_run_target,
            input_summary=input_summary,
            resume_from_run_id=(
                None if resume_context is None else resume_context.previous_run_id
            ),
        )
        await self._run_coordinator.submit_run(request)
        return request

    async def _prepare_local_daemon_runtime(
        self,
        session: Session,
        *,
        consumer: _WireConsumer,
        request: RunRequest,
    ) -> LocalDaemonRuntimeBinding:
        pipeline = session.runtime_pipeline
        ctx = session.runtime_ctx
        adapter = session.runtime_adapter
        environment = self._resolve_local_daemon_environment(request.target)
        workspace_root = self._environment_workspace_root(environment)
        if pipeline is not None and ctx is not None and adapter is not None:
            cached_workspace_root = self._runtime_environment_workspace_root(ctx)
            if (
                cached_workspace_root is not None
                and workspace_root is not None
                and cached_workspace_root != workspace_root
            ):
                await self._close_runtime(session)
                pipeline = None
                ctx = None
                adapter = None
        if pipeline is None or ctx is None or adapter is None:
            approval_mode_map = {
                ApprovalPolicy.YOLO: "yolo",
                ApprovalPolicy.INTERACTIVE: "interactive",
                ApprovalPolicy.AUTO: "auto",
            }
            pipeline, ctx = self._create_agent_for_session(
                workspace_root=workspace_root,
                environment=environment,
                model_override=session.model_name,
                provider_override=session.provider_name,
                base_url_override=session.base_url,
                max_steps_override=session.max_steps,
                approval_mode_override=approval_mode_map[session.approval_policy],
                session_id_override=session.id,
                run_id_override=request.run_id,
                api_key=None,
                tape=await self._restore_tape(session.tape_id),
            )
            session.tape_id = ctx.tape.tape_id
            await self._persist_session_async(session)
            ctx.runtime_message_bus = session.runtime_message_bus
            ctx.config["wire_consumer"] = None
            ctx.config["agent_id"] = ""

            llm_plugin = pipeline._registry.get("llm_provider")
            if session.provider is not None:
                llm_plugin._instance = session.provider

            adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
            session.runtime_pipeline = pipeline
            session.runtime_ctx = ctx
            session.runtime_adapter = adapter
        return LocalDaemonRuntimeBinding(
            pipeline=pipeline,
            ctx=ctx,
            adapter=adapter,
        )

    def _invalidate_runtime(self, session: Session) -> None:
        session.runtime_pipeline = None
        session.runtime_ctx = None
        session.runtime_adapter = None

    def _hydrate_session(self, session: Session) -> Session:
        approval_store = self._approval_stores.get(session.id)
        if approval_store is None:
            approval_store = session.approval_store
            self._approval_stores[session.id] = approval_store
        session.approval_store = approval_store
        session.approval_coordinator = ApprovalCoordinator(approval_store)
        self._session_cache[session.id] = session
        return session

    async def create_session(
        self,
        repo_path: Path | None = None,
        origin: dict[str, str] | None = None,
        approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        provider: Any | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        max_steps: int = 30,
        enable_parallel: bool = True,
        max_parallel: int = 5,
        execution_binding: ExecutionBinding | None = None,
    ) -> str:
        """Create a new agent session.

        Args:
            repo_path: Path to the repository root (default: current directory)
            approval_policy: Policy for tool execution approval
            provider: Explicit LLM provider override for tests or custom sessions
            provider_name: Restart-safe provider identifier for later rehydration
            model_name: Restart-safe model identifier for later rehydration
            base_url: Restart-safe provider base URL for later rehydration
            max_steps: Maximum steps per turn
            enable_parallel: Enable parallel tool execution
            max_parallel: Maximum number of parallel tool executions
            execution_binding: Explicit workspace execution binding. If omitted,
                a local binding is derived from repo_path or the current directory.

        Returns:
            The session ID
        """
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        approval_store = ApprovalStore()
        self._approval_stores[session_id] = approval_store

        if provider is None:
            cfg = core_config.load_config()
            if provider_name is None:
                provider_name = cfg.provider
            if model_name is None:
                model_name = cfg.model
            if base_url is None:
                base_url = cfg.base_url

        resolved_repo_path = repo_path.resolve() if repo_path is not None else None
        if execution_binding is None:
            binding = LocalExecutionBinding(
                workspace_root=(
                    str(resolved_repo_path)
                    if resolved_repo_path is not None
                    else str(Path.cwd().resolve())
                )
            )
        else:
            binding = execution_binding

        session = Session(
            id=session_id,
            approval_store=approval_store,
            created_at=now,
            last_activity=now,
            repo_path=resolved_repo_path,
            origin=None if origin is None else dict(origin),
            execution_binding=binding,
            approval_policy=approval_policy,
            provider=provider,
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
            max_steps=max_steps,
            task=None,
        )

        async with self._lock:
            try:
                await self._persist_session_async(session)
                await self._persist_workspace_record_for_session(session)
                await self._acquire_owner_for_session(session_id)
            except BaseException:
                self._session_cache.pop(session_id, None)
                try:
                    await self._release_owner_lease_for_session(session_id)
                except asyncio.CancelledError:
                    pass
                except BaseException:
                    logger.exception(
                        "Failed to release partially created owner lease during rollback: %s",
                        session_id,
                    )
                try:
                    await self._run_store_io(self._store.delete, session_id)
                except asyncio.CancelledError:
                    pass
                except BaseException:
                    logger.exception(
                        "Failed to delete partially created session during rollback: %s",
                        session_id,
                    )
                self._approval_stores.pop(session_id, None)
                raise

        logger.info(f"Created session: {session_id}")
        return session_id

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID.

        Args:
            session_id: The session ID

        Returns:
            The Session object

        Raises:
            KeyError: If session not found
        """
        session = self._session_cache.get(session_id)
        if session is not None:
            return session
        loaded = self._store.load(session_id)
        if loaded is None:
            raise KeyError(f"Session not found: {session_id}")
        return self._hydrate_session(
            Session.from_store_data(cast(dict[str, Any], loaded))
        )

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists.

        Args:
            session_id: The session ID

        Returns:
            True if session exists, False otherwise
        """
        if session_id in self._session_cache:
            return True
        return self._store.load(session_id) is not None

    def register_session(self, session: Session) -> None:
        self._close_runtime_sync_safe(session)
        self._approval_stores[session.id] = session.approval_store
        self._persist_session(session)

    def remove_session(self, session_id: str) -> None:
        if not self.has_session(session_id):
            raise KeyError(f"Session not found: {session_id}")
        session = self.get_session(session_id)
        self._close_runtime_sync_safe(session)
        self._cleanup_provisioned_cloud_binding(session)
        self._session_cache.pop(session_id, None)
        self._store.delete(session_id)
        self._approval_stores.pop(session_id, None)
        self._session_turn_locks.pop(session_id, None)

    def clear_sessions(self) -> None:
        cleared_session_ids = set(self._session_cache)
        for session in list(self._session_cache.values()):
            self._close_runtime_sync_safe(session)
            self._cleanup_provisioned_cloud_binding(session)
        for session_id in list(self._store.list_sessions()):
            if session_id not in cleared_session_ids:
                session = self.get_session(session_id)
                self._close_runtime_sync_safe(session)
                self._cleanup_provisioned_cloud_binding(session)
                self._session_cache.pop(session_id, None)
            self._store.delete(session_id)
        self._session_cache.clear()
        self._approval_stores.clear()
        self._session_turn_locks.clear()

    def add_event_queue(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = self.get_session(session_id)
        session.event_queues.append(queue)

    def remove_event_queue(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = self.get_session(session_id)
        if queue in session.event_queues:
            session.event_queues.remove(queue)

    async def broadcast_event(
        self,
        session_id: str,
        event: dict[str, Any],
    ) -> None:
        session = self.get_session(session_id)
        result = session.broadcast_event_nowait(event)
        if result.full_pruned_count:
            logger.info(
                "Pruned %d full event queue(s) for session %s",
                result.full_pruned_count,
                session_id,
            )
        if result.failed_pruned_count:
            logger.info(
                "Pruned %d failed event queue(s) for session %s",
                result.failed_pruned_count,
                session_id,
            )

    def has_approval_request(self, session_id: str) -> bool:
        return (
            self.get_session(session_id).approval_coordinator.pending_request
            is not None
        )

    def matches_approval_request(self, session_id: str, request_id: str) -> bool:
        session = self.get_session(session_id)
        return session.approval_coordinator.get_request(request_id) is not None

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.

        Args:
            session_id: The session ID to close

        Raises:
            KeyError: If session not found
        """
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)

            # Cancel any running task
            if session.task and not session.task.done():
                session.task.cancel()
                try:
                    await asyncio.wait_for(session.task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                if not session.task.done():
                    raise RuntimeError(
                        f"Session task for {session_id} did not stop after cancellation"
                    )

            await self._remove_session_async_no_lock(session_id)

        logger.info(f"Closed session: {session_id}")

    async def shutdown_session_runtime(self, session_id: str) -> None:
        """Release runtime resources without deleting persisted session metadata."""
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)

            if session.task and not session.task.done():
                session.task.cancel()
                try:
                    await asyncio.wait_for(session.task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                if not session.task.done():
                    raise RuntimeError(
                        f"Session task for {session_id} did not stop after cancellation"
                    )

            await self._close_runtime(session)
            session.task = None
            session.turn_in_progress = False
            await self._persist_session_async(session)

    async def cancel_session_turn(self, session_id: str) -> CancelTurnResult:
        """Request cancellation for the active turn without closing the session."""
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            task = session.task
            if isinstance(session.execution_binding, ExternalWorkerBinding):
                if session.current_turn_id is None or not session.turn_in_progress:
                    session.turn_status = "idle"
                    session.turn_in_progress = False
                    session.last_activity = datetime.now(UTC)
                    await self._persist_session_async(session)
                    return CancelTurnResult(
                        session_id=session_id,
                        turn_id=session.current_turn_id,
                        status="idle",
                    )
                if self._runtime_store is not None:
                    run = await self.load_runtime_run(session.current_turn_id)
                    metadata = dict(run.metadata)
                    metadata["cancel_requested_at"] = datetime.now(UTC).isoformat()
                    if run.status in {"requested", "expired"}:
                        await self._runtime_store.update_agent_run(
                            run.run_id,
                            status="cancelled",
                            ended_at=datetime.now(UTC),
                            metadata=cast(JSONObject, metadata),
                            result=run.result,
                            error="cancelled before claim",
                        )
                        session.turn_status = "idle"
                        session.turn_in_progress = False
                        session.last_activity = datetime.now(UTC)
                        await self._persist_session_async(session)
                        return CancelTurnResult(
                            session_id=session_id,
                            turn_id=session.current_turn_id,
                            status="cancelled",
                        )
                    await self._runtime_store.update_agent_run(
                        run.run_id,
                        status="cancelling",
                        ended_at=run.ended_at,
                        metadata=cast(JSONObject, metadata),
                        result=run.result,
                        error=run.error,
                    )
                session.turn_status = "cancelling"
                session.turn_in_progress = True
                session.last_activity = datetime.now(UTC)
                await self._persist_session_async(session)
                return CancelTurnResult(
                    session_id=session_id,
                    turn_id=session.current_turn_id,
                    status="cancelling",
                )
            if task is None or task.done():
                status: CancelTurnStatus
                if session.turn_status == "cancelling":
                    status = "cancelling"
                elif session.turn_status in {"cancelled", "failed"}:
                    status = cast(CancelTurnStatus, session.turn_status)
                else:
                    session.turn_status = "idle"
                    status = "idle"
                session.turn_in_progress = False
                session.last_activity = datetime.now(UTC)
                await self._persist_session_async(session)
                return CancelTurnResult(
                    session_id=session_id,
                    turn_id=session.current_turn_id,
                    status=status,
                )

            if session.current_turn_id is None:
                session.current_turn_id = uuid.uuid4().hex
            session.turn_status = "cancelling"
            session.turn_in_progress = True
            session.last_activity = datetime.now(UTC)
            await self._persist_session_async(session)
            task.cancel()
            _ = asyncio.create_task(
                self._observe_cancelled_turn(session_id=session_id, task=task)
            )
            return CancelTurnResult(
                session_id=session_id,
                turn_id=session.current_turn_id,
                status="cancelling",
            )

    async def _observe_cancelled_turn(
        self,
        *,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        final_status: TurnStatus = "cancelled"
        try:
            await task
        except asyncio.CancelledError:
            final_status = "cancelled"
        except Exception:
            logger.exception("Cancelled session turn failed during cleanup")
            final_status = "failed"

        async with self._lock:
            try:
                session = await self.get_session_async(session_id)
            except KeyError:
                return
            if session.task is not task:
                return
            session.task = None
            session.turn_in_progress = False
            session.turn_status = final_status
            session.last_activity = datetime.now(UTC)
            await self._persist_session_async(session)

    async def close(self) -> None:
        await self._close_resource_async(self._store)
        if self._owns_pg_pool:
            await self._close_resource_async(self._pg_pool)

    async def run_agent(
        self,
        session_id: str,
        prompt: str,
        *,
        run_id_override: str | None = None,
        resume_context: SessionResumeContext | None = None,
    ) -> None:
        turn_lock = self._turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")

        async with turn_lock:
            if self._workspace_export_in_progress(session_id):
                raise RuntimeError("turn already in progress")
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            session.last_activity = datetime.now(UTC)
            session.turn_in_progress = True
            session.turn_status = "running"
            run_id = run_id_override or uuid.uuid4().hex
            started_at = datetime.now(UTC)
            session.current_turn_id = run_id
            session.last_failure_details = None
            await self._persist_session_async(session)
            agent_run_created = False
            observation_recorder: AgentObservationRecorder | None = None
            turn_error_handled = False
            turn_error_handler_failed = False

            async def _handle_fatal_local_daemon_turn_error(
                exc: FatalToolExecutionError,
            ) -> None:
                if observation_recorder is not None:
                    observation_recorder.fail_turn(error_type=type(exc).__name__)
                if agent_run_created:
                    await self._finish_runtime_agent_run(
                        session,
                        run_id=run_id,
                        status="failed",
                        result={},
                        error=str(exc),
                        resume_context=resume_context,
                    )
                session.turn_status = "failed"
                session.last_failure_details = f"Fatal tool execution failed: {exc}"
                await self._close_runtime(session)

            async def _handle_cancelled_local_daemon_turn_error() -> None:
                if observation_recorder is not None:
                    observation_recorder.cancel_turn()
                if agent_run_created:
                    await self._finish_runtime_agent_run(
                        session,
                        run_id=run_id,
                        status="cancelled",
                        result={},
                        error="cancelled",
                        resume_context=resume_context,
                    )

            async def _handle_generic_local_daemon_turn_error(
                exc: Exception,
            ) -> None:
                if observation_recorder is not None:
                    observation_recorder.fail_turn(error_type=type(exc).__name__)
                if agent_run_created:
                    await self._finish_runtime_agent_run(
                        session,
                        run_id=run_id,
                        status="failed",
                        result={},
                        error=str(exc),
                        resume_context=resume_context,
                    )
                session.turn_status = "failed"
                session.last_failure_details = f"HTTP session turn failed: {exc}"
                await self._close_runtime(session)
                logger.exception("HTTP session turn failed")
                await self._send_session_wire_message(
                    session,
                    StreamDelta(
                        session_id=session_id,
                        agent_id="",
                        content=f"Error: {exc}",
                    ),
                )
                await self._send_session_wire_message(
                    session,
                    TurnEnd(
                        session_id=session_id,
                        agent_id="",
                        turn_id=run_id,
                        completion_status=CompletionStatus.ERROR,
                    ),
                )

            async def _on_local_daemon_turn_error(
                binding: LocalDaemonRuntimeBinding,
                exc: BaseException,
            ) -> None:
                del binding
                nonlocal turn_error_handled, turn_error_handler_failed
                if isinstance(exc, FatalToolExecutionError):
                    try:
                        await _handle_fatal_local_daemon_turn_error(exc)
                    except BaseException:
                        turn_error_handler_failed = True
                        raise
                    turn_error_handled = True
                    return
                if isinstance(exc, asyncio.CancelledError):
                    try:
                        await _handle_cancelled_local_daemon_turn_error()
                    except BaseException:
                        turn_error_handler_failed = True
                        raise
                    turn_error_handled = True
                    return
                if isinstance(exc, Exception):
                    try:
                        await _handle_generic_local_daemon_turn_error(exc)
                    except BaseException:
                        turn_error_handler_failed = True
                        raise
                    turn_error_handled = True

            try:
                consumer = self._make_session_consumer(session)
                run_request = await self._submit_runtime_run_request(
                    session,
                    run_id=run_id,
                    prompt=prompt,
                    resume_context=resume_context,
                )

                if not isinstance(run_request.target.executor, LocalDaemonExecutorRef):
                    agent_run_created = await self._create_runtime_agent_run(
                        session,
                        run_id=run_id,
                        started_at=started_at,
                        resume_context=resume_context,
                    )
                    if agent_run_created:
                        await self._update_runtime_agent_run(
                            session,
                            run_id=run_id,
                            status="running",
                            ended_at=None,
                            result={},
                            error=None,
                            resume_context=resume_context,
                        )
                    executor_kind = _executor_ref_kind(run_request.target.executor)
                    raise UnsupportedRuntimeExecutorError(
                        "executor target "
                        f"{executor_kind!r} does not have a local runtime path; "
                        "control plane cannot execute runtime directly"
                    )

                async def _before_local_daemon_turn(
                    binding: LocalDaemonRuntimeBinding,
                ) -> None:
                    nonlocal agent_run_created, observation_recorder
                    ctx = binding.ctx
                    adapter = binding.adapter
                    self._bind_root_run_identity(
                        session,
                        ctx,
                        run_id,
                        resume_context=resume_context,
                    )
                    agent_run_created = await self._create_runtime_agent_run(
                        session,
                        run_id=run_id,
                        started_at=started_at,
                        resume_context=resume_context,
                    )
                    if agent_run_created:
                        await self._update_runtime_agent_run(
                            session,
                            run_id=run_id,
                            status="running",
                            ended_at=None,
                            result={},
                            error=None,
                            resume_context=resume_context,
                        )
                    set_consumer = getattr(adapter, "set_consumer", None)
                    if callable(set_consumer):
                        set_consumer(consumer)
                    ctx.runtime_message_bus = session.runtime_message_bus
                    ctx.config["wire_consumer"] = consumer
                    self._bind_subagent_message_publisher(ctx)
                    observation_recorder = self._start_agent_observation(
                        session=session,
                        ctx=ctx,
                        run_id=run_id,
                        prompt=prompt,
                        resume_context=resume_context,
                    )

                async def _after_local_daemon_turn(
                    binding: LocalDaemonRuntimeBinding,
                    outcome: object,
                ) -> None:
                    ctx = binding.ctx
                    if self._runtime_store is not None:
                        turn_outcome = self._require_turn_outcome(outcome)
                        turn_status = self._status_from_turn_outcome(turn_outcome)
                        session.tape_id = ctx.tape.tape_id
                        await self._save_runtime_message_snapshot(
                            session,
                            ctx,
                            run_id=run_id,
                        )
                        await self._finish_runtime_agent_run(
                            session,
                            run_id=run_id,
                            status=turn_status,
                            result=self._result_from_turn_outcome(turn_outcome),
                            error=turn_outcome.error,
                            resume_context=resume_context,
                        )
                        if turn_status == "failed":
                            session.turn_status = "failed"
                            reason = (
                                turn_outcome.error or turn_outcome.stop_reason.value
                            )
                            session.last_failure_details = (
                                f"Agent turn failed: {reason}"
                            )
                        else:
                            session.last_failure_details = None
                    else:
                        session.tape_id = ctx.tape.tape_id
                        turn_status = "completed"
                        if isinstance(outcome, TurnOutcome):
                            turn_status = self._status_from_turn_outcome(outcome)
                            if turn_status == "failed":
                                session.turn_status = "failed"
                                reason = outcome.error or outcome.stop_reason.value
                                session.last_failure_details = (
                                    f"Agent turn failed: {reason}"
                                )
                            else:
                                session.last_failure_details = None
                        else:
                            session.last_failure_details = None
                    self._complete_agent_observation(
                        observation_recorder,
                        ctx=ctx,
                        turn_status=turn_status,
                    )
                    await self._persist_session_async(session)

                await self._local_daemon_executor.execute_runtime(
                    LocalDaemonRuntimeExecution(
                        request=run_request,
                        runtime_provider=_SessionLocalDaemonRuntimeProvider(
                            prepare=lambda request: self._prepare_local_daemon_runtime(
                                session,
                                consumer=consumer,
                                request=request,
                            )
                        ),
                        prompt=prompt,
                        before_turn=_before_local_daemon_turn,
                        after_turn=_after_local_daemon_turn,
                        on_turn_error=_on_local_daemon_turn_error,
                    )
                )
            except FatalToolExecutionError as exc:
                if turn_error_handler_failed:
                    raise
                if not turn_error_handled:
                    await _handle_fatal_local_daemon_turn_error(exc)
                raise
            except asyncio.CancelledError:
                if turn_error_handler_failed:
                    raise
                if not turn_error_handled:
                    await _handle_cancelled_local_daemon_turn_error()
                raise
            except Exception as exc:
                if turn_error_handler_failed:
                    raise
                if not turn_error_handled:
                    await _handle_generic_local_daemon_turn_error(exc)
            finally:
                current_task = asyncio.current_task()
                if session.task is None or session.task is not current_task:
                    session.turn_in_progress = False
                    if session.turn_status == "running":
                        session.turn_status = "idle"
                session.last_activity = datetime.now(UTC)
                await self._persist_session_async(session)

    async def _consume_approval_decisions_for_session(
        self,
        session: Session,
        *,
        limit: int | None = None,
    ) -> ApprovalDecisionConsumptionResult:
        consumer = ApprovalDecisionConsumer(
            session_id=session.id,
            coordinator=session.approval_coordinator,
        )
        result = await consumer.consume(
            session.runtime_message_bus,
            session.approval_decision_cursor,
            limit=limit,
        )
        if result.applied_request_ids or not result.deferred_message_ids:
            session.approval_decision_cursor = result.cursor
        if result.applied_request_ids:
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_event.set()
        return result

    async def _published_approval_decision(
        self,
        session: Session,
        request_id: str,
    ) -> _PublishedApprovalDecision | None:
        message_id = _approval_decision_message_id(session.id, request_id)
        batch = await session.runtime_message_bus.consume_after(
            RuntimeMessageCursor(),
            kinds={RuntimeMessageKind.APPROVAL_DECISION},
        )
        for item in batch.messages:
            if item.message.message_id != message_id:
                continue
            response = approval_response_from_runtime_payload(
                session_id=session.id,
                message_id=item.message.message_id,
                payload=item.message.payload,
            )
            if response is None:
                return None
            return _PublishedApprovalDecision(
                sequence=item.sequence,
                response=response,
            )
        return None

    async def _apply_published_approval_decision(
        self,
        session: Session,
        request_id: str,
        decision: _PublishedApprovalDecision,
    ) -> ApprovalResponse | None:
        already_consumed = (
            decision.sequence <= session.approval_decision_cursor.sequence
        )
        applied = False
        if session.approval_coordinator.get_request(request_id) is not None:
            applied = session.approval_coordinator.respond(decision.response)
            if applied and not already_consumed:
                session.approval_decision_cursor = RuntimeMessageCursor(
                    max(
                        session.approval_decision_cursor.sequence,
                        decision.sequence,
                    )
                )
        if not applied and not already_consumed:
            return None

        session.last_activity = datetime.now(UTC)
        session.pending_approval = session.approval_coordinator.projection()
        session.approval_response = _approval_response_projection(decision.response)
        session.approval_event.set()
        await self._persist_session_async(session)
        await self._resolve_approval_interaction(
            session,
            request_id,
            decision.response,
        )
        if not applied:
            logger.info(
                "approval_decision for session %s request %s was already published; keeping the first decision",
                session.id,
                request_id,
            )
        return decision.response

    async def submit_approval_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: Literal["once", "session", "always"] = "once",
    ) -> ApprovalResponse | None:
        """Submit an approval response for a pending request.

        Uses the session's ApprovalStore to record the response.

        Args:
            session_id: The session ID
            request_id: The approval request ID
            approved: Whether the request is approved
            feedback: Optional feedback message

        Returns:
            The stored/applied approval response, or None if no matching request exists

        Raises:
            KeyError: If session not found
        """
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        message_id = _approval_decision_message_id(session_id, request_id)

        published_decision = await self._published_approval_decision(
            session,
            request_id,
        )
        if published_decision is not None:
            return await self._apply_published_approval_decision(
                session,
                request_id,
                published_decision,
            )

        if session.approval_coordinator.get_request(request_id) is None:
            logger.warning(
                f"Approval submission failed for session {session_id}: request {request_id} not found"
            )
            return None

        try:
            await session.runtime_message_bus.publish(
                RuntimeMessage(
                    message_id=message_id,
                    kind=RuntimeMessageKind.APPROVAL_DECISION,
                    payload={
                        "session_id": session_id,
                        "request_id": request_id,
                        "approved": approved,
                        "feedback": feedback,
                        "scope": scope,
                    },
                )
            )
        except DuplicateRuntimeMessageError as exc:
            if exc.message_id != message_id:
                raise
            published_decision = await self._published_approval_decision(
                session,
                request_id,
            )
            if published_decision is None:
                raise RuntimeError(
                    f"duplicate approval_decision {message_id!r} was not readable"
                ) from exc
            logger.info(
                "approval_decision already published for session %s request %s",
                session_id,
                request_id,
            )

        if published_decision is None:
            published_decision = await self._published_approval_decision(
                session,
                request_id,
            )
        if published_decision is None:
            raise RuntimeError(f"approval_decision {message_id!r} was not readable")

        result = await self._consume_approval_decisions_for_session(session)
        success = request_id in result.applied_request_ids
        session.last_activity = datetime.now(UTC)

        if success:
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_response = _approval_response_projection(
                published_decision.response
            )
            session.approval_event.set()
            await self._persist_session_async(session)
            await self._resolve_approval_interaction(
                session,
                request_id,
                published_decision.response,
            )
            logger.info(
                "Approval submitted for session %s: %s",
                session_id,
                published_decision.response.approved,
            )
        else:
            logger.warning(
                "approval_decision for session %s request %s was not applied (validation failure or race)",
                session_id,
                request_id,
            )
            if result.applied_request_ids:
                # A consume batch can apply other requests even when this
                # specific submission is skipped.
                await self._persist_session_async(session)
            return None

        return published_decision.response

    async def submit_approval(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: Literal["once", "session", "always"] = "once",
    ) -> bool:
        response = await self.submit_approval_response(
            session_id=session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )
        return response is not None

    async def publish_subagent_message(
        self,
        session_id: str,
        text: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if message_id is not None and not message_id:
            raise ValueError("message_id must be None or a non-empty string")

        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            effective_message_id = message_id or _subagent_message_id(session_id)
            payload: dict[str, Any] = {"text": text}
            if metadata is not None:
                payload["metadata"] = dict(metadata)

            try:
                await session.runtime_message_bus.publish(
                    RuntimeMessage(
                        message_id=effective_message_id,
                        kind=RuntimeMessageKind.SUBAGENT_MESSAGE,
                        payload=payload,
                    )
                )
            except DuplicateRuntimeMessageError as exc:
                if exc.message_id != effective_message_id:
                    raise
                logger.info(
                    "subagent_message already published for session %s message %s",
                    session_id,
                    effective_message_id,
                )
            session.last_activity = datetime.now(UTC)
            await self._persist_session_async(session)
        return True

    async def wait_for_http_approval(
        self,
        session_id: str,
        approval_req: ApprovalRequest,
        timeout_seconds: float,
    ) -> ApprovalResponse:
        await self._assert_owner(session_id)
        if not await self.has_session_async(session_id):
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=False,
                feedback="Session not found",
            )

        session = await self.get_session_async(session_id)
        if not session.turn_in_progress:
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=False,
                feedback="Approval timeout or error",
            )

        if session.approval_coordinator.is_session_approved(approval_req):
            response = ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=True,
                scope="session",
            )
            await self._create_approval_interaction(session, approval_req)
            await self._resolve_approval_interaction(
                session,
                approval_req.request_id,
                response,
            )
            return response

        session.approval_coordinator.add_request(approval_req)
        session.pending_approval = session.approval_coordinator.projection()
        session.approval_event.clear()
        session.approval_response = None
        await self._persist_session_async(session)
        await self._create_approval_interaction(session, approval_req)
        published_decision = await self._published_approval_decision(
            session,
            approval_req.request_id,
        )
        if published_decision is not None:
            response = await self._apply_published_approval_decision(
                session,
                approval_req.request_id,
                published_decision,
            )
            if response is not None:
                return response

        try:
            response = await session.approval_coordinator.wait_for_response(
                approval_req.request_id,
                float(timeout_seconds),
            )
            if response is not None:
                await self._resolve_approval_interaction(
                    session,
                    approval_req.request_id,
                    response,
                )
                return response
        finally:
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_response = None
            _ = session.approval_event.set()
            await self._persist_session_async(session)

        timeout_response = ApprovalResponse(
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Approval timeout or error",
        )
        await self._resolve_approval_interaction(
            session,
            approval_req.request_id,
            timeout_response,
            status="timed_out",
        )
        return timeout_response

    def list_sessions(self) -> list[str]:
        """List all active session IDs.

        Returns:
            List of session IDs
        """
        return self._store.list_sessions()

    def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Get session information.

        Args:
            session_id: The session ID

        Returns:
            Dictionary with session info

        Raises:
            KeyError: If session not found
        """
        session = self.get_session(session_id)
        return session.as_dict()

    async def cleanup_idle_sessions(self, max_idle_minutes: int = 30) -> list[str]:
        """Clean up sessions that have been idle for too long.

        Args:
            max_idle_minutes: Maximum idle time in minutes

        Returns:
            List of closed session IDs
        """
        now = datetime.now(UTC)
        closed: list[str] = []
        session_ids = await self.list_sessions_async()

        for session_id in session_ids:
            try:
                session = await self.get_session_async(session_id)
                last_activity = session.last_activity
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=UTC)
                idle_time = now - last_activity
                if idle_time.total_seconds() > max_idle_minutes * 60:
                    await self.close_session(session_id)
                    closed.append(session_id)
            except KeyError:
                # Session already closed
                pass

        if closed:
            logger.info(f"Cleaned up {len(closed)} idle sessions: {closed}")

        return closed

    def _is_local_daemon_run_target(self, target: RunTarget | None) -> bool:
        if target is None:
            return False
        return isinstance(target.executor, LocalDaemonExecutorRef)

    def _runtime_preparation_request(self, session: Session) -> RunRequest:
        if session.default_run_target is None:
            raise RuntimeError("session is missing default_run_target")
        return RunRequest(
            session_id=session.id,
            run_id=f"runtime-prepare-{uuid.uuid4().hex}",
            target=session.default_run_target,
            metadata={"purpose": "runtime_preparation"},
        )

    async def _build_session_runtime_direct(
        self,
        session: Session,
        *,
        target: RunTarget | None = None,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None = None,
        max_steps: int | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> tuple[Any, Any, PipelineAdapter]:
        approval_mode_map = {
            ApprovalPolicy.YOLO: "yolo",
            ApprovalPolicy.INTERACTIVE: "interactive",
            ApprovalPolicy.AUTO: "auto",
        }
        resolved_provider_name = (
            session.provider_name if provider_name is None else provider_name
        )
        resolved_model_name = session.model_name if model_name is None else model_name
        resolved_base_url = session.base_url if base_url is None else base_url
        resolved_max_steps = session.max_steps if max_steps is None else max_steps
        resolved_approval_policy = (
            session.approval_policy if approval_policy is None else approval_policy
        )
        runtime_target = session.default_run_target if target is None else target
        environment = self._resolve_environment_for_run_target(runtime_target)
        workspace_root = self._environment_workspace_root(environment)

        consumer = self._make_session_consumer(session)
        pipeline, ctx = self._create_agent_for_session(
            workspace_root=workspace_root,
            environment=environment,
            model_override=resolved_model_name,
            provider_override=resolved_provider_name,
            base_url_override=resolved_base_url,
            max_steps_override=resolved_max_steps,
            approval_mode_override=approval_mode_map[resolved_approval_policy],
            session_id_override=session.id,
            api_key=None,
            tape=await self._restore_tape(session.tape_id),
        )
        ctx.config["wire_consumer"] = consumer
        ctx.config["agent_id"] = ""
        self._bind_subagent_message_publisher(ctx)

        llm_plugin = pipeline._registry.get("llm_provider")
        provider_model_name = getattr(session.provider, "model_name", None)
        if (
            session.provider is not None
            and session.provider_name == resolved_provider_name
            and provider_model_name == resolved_model_name
            and session.base_url == resolved_base_url
        ):
            llm_plugin._instance = session.provider

        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
        try:
            await adapter.initialize()
        except Exception:
            await self._close_runtime_adapter(adapter)
            raise
        return pipeline, ctx, adapter

    async def _build_session_runtime(
        self,
        session: Session,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None = None,
        max_steps: int | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> tuple[Any, Any, PipelineAdapter]:
        if not self._is_local_daemon_run_target(session.default_run_target):
            return await self._build_session_runtime_direct(
                session,
                model_name=model_name,
                provider_name=provider_name,
                base_url=base_url,
                max_steps=max_steps,
                approval_policy=approval_policy,
            )

        async def prepare_runtime(request: RunRequest) -> LocalDaemonRuntimeBinding:
            pipeline, ctx, adapter = await self._build_session_runtime_direct(
                session,
                target=request.target,
                model_name=model_name,
                provider_name=provider_name,
                base_url=base_url,
                max_steps=max_steps,
                approval_policy=approval_policy,
            )
            return LocalDaemonRuntimeBinding(
                pipeline=pipeline,
                ctx=ctx,
                adapter=adapter,
            )

        binding = await self._local_daemon_executor.prepare_runtime(
            LocalDaemonRuntimePreparation(
                request=self._runtime_preparation_request(session),
                runtime_provider=_SessionLocalDaemonRuntimeProvider(
                    prepare=prepare_runtime,
                ),
            )
        )
        return (
            binding.pipeline,
            binding.ctx,
            cast(PipelineAdapter, binding.adapter),
        )

    async def ensure_session_runtime(self, session_id: str) -> Any:
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        if session.runtime_ctx is not None and session.runtime_adapter is not None:
            return session.runtime_ctx

        pipeline, ctx, adapter = await self._build_session_runtime(session)

        session.runtime_pipeline = pipeline
        session.runtime_ctx = ctx
        session.runtime_adapter = adapter
        session.tape_id = ctx.tape.tape_id
        await self._persist_session_async(session)
        return ctx

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str,
    ) -> Session:
        turn_lock = self._turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")

        async with turn_lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            if session.task and not session.task.done():
                raise RuntimeError("turn already in progress")
            if session.turn_in_progress:
                raise RuntimeError("turn already in progress")

            old_provider = session.provider
            old_model_name = session.model_name
            old_tape_id = session.tape_id
            old_runtime_pipeline = session.runtime_pipeline
            old_runtime_ctx = session.runtime_ctx
            old_runtime_adapter = session.runtime_adapter

            pipeline, ctx, adapter = await self._build_session_runtime(
                session,
                model_name=model_name,
            )

            session.provider = None
            session.model_name = model_name
            session.runtime_pipeline = pipeline
            session.runtime_ctx = ctx
            session.runtime_adapter = adapter
            session.tape_id = ctx.tape.tape_id
            try:
                await self._persist_session_async(session)
            except Exception:
                session.provider = old_provider
                session.model_name = old_model_name
                session.runtime_pipeline = old_runtime_pipeline
                session.runtime_ctx = old_runtime_ctx
                session.runtime_adapter = old_runtime_adapter
                session.tape_id = old_tape_id
                await self._close_runtime_adapter(adapter)
                raise

            try:
                await self._close_runtime_adapter(old_runtime_adapter)
            except Exception:
                logger.warning(
                    "Failed to close previous runtime adapter for session %s",
                    session_id,
                    exc_info=True,
                )
            return session

    async def capture_checkpoint(
        self,
        session_id: str,
        *,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        turn_lock = self._turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")

        async with turn_lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            if session.turn_in_progress or (session.task and not session.task.done()):
                raise RuntimeError("turn already in progress")

            ctx = await self.ensure_session_runtime(session_id)
            payload = dict(extra or {})
            if _CHECKPOINT_SESSION_CONFIG_KEY in payload:
                raise ValueError(
                    f"'{_CHECKPOINT_SESSION_CONFIG_KEY}' is a reserved checkpoint metadata key and cannot be provided via extra"
                )
            payload[_CHECKPOINT_SESSION_CONFIG_KEY] = (
                _serialize_checkpoint_session_config(session)
            )
            checkpoint = await self._checkpoint_service.capture(
                ctx, label=label, extra=payload
            )
            session.tape_id = ctx.tape.tape_id
            await self._persist_session_async(session)
            return checkpoint

    async def export_workspace_archive(
        self,
        session_id: str,
        export_archive: Callable[[CloudWorkspaceBinding], T],
    ) -> T:
        turn_lock = self._turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")

        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        if session.turn_in_progress or (session.task and not session.task.done()):
            raise RuntimeError("turn already in progress")
        if not isinstance(session.execution_binding, CloudWorkspaceBinding):
            raise ValueError("Workspace export requires cloud session")
        self._begin_workspace_export(session_id)
        try:
            result = await asyncio.to_thread(export_archive, session.execution_binding)
            await self._assert_owner(session_id)
            return result
        finally:
            self._end_workspace_export(session_id)

    async def list_checkpoints(self, session_id: str) -> list[CheckpointMeta]:
        session = await self.get_session_async(session_id)
        if session.tape_id is None:
            return []
        return await self._checkpoint_service.list(session.tape_id)

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        turn_lock = self._turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")

        async with turn_lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            if session.turn_in_progress or (session.task and not session.task.done()):
                raise RuntimeError("turn already in progress")
            await self._restore_checkpoint(session, checkpoint_id)
