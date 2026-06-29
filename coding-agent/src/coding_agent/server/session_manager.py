"""SessionManager for managing agent sessions."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from inspect import isawaitable
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, TypeVar, cast

from agentkit.environment import Environment
from agentkit.errors import ConfigError
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
from agentkit.storage.protocols import CheckpointStore, TapeStore
from agentkit.storage.protocols import TapeDebugStore, TapeInfo, TapeSearchResult
from agentkit.tape.models import Anchor
from agentkit.tape.store import ForkTapeStore
from agentkit.tape.tape import Tape
from coding_agent.adapter import PipelineAdapter
from coding_agent.observability.agent import (
    AgentObservationStore,
    JsonlAgentObservationStore,
)
from coding_agent.approval import (
    ApprovalInteractionService,
    ApprovalCoordinator,
    ApprovalDecisionService,
    ApprovalRequestService,
    ApprovalPolicy,
)
from coding_agent.approval.store import ApprovalStore
from coding_agent.core import config as core_config
from coding_agent.stores.local import (
    DURABLE_STORAGE_BACKEND_KEYS,
    DURABLE_STORAGE_PATH_KEYS,
    durable_storage_backend_values,
    local_sqlite_storage_config,
    local_sqlite_path_from_storage_config,
    normalize_storage_path,
    storage_uses_local_sqlite_bundle,
    with_local_sqlite_bundle_paths,
)
from coding_agent.stores.durable_local import (
    FencedSQLiteCheckpointStore,
    FencedSQLiteRuntimeStore,
    FencedSQLiteTapeStore,
    FencedSQLiteTopicStore,
    SQLiteLocalDurableStore,
)
from coding_agent.stores.durable_pg import (
    FencedPGCheckpointStore,
    FencedPGRuntimeStore,
    FencedPGTapeStore,
    FencedPGTopicStore,
    PGDurableStore,
)
from coding_agent.plugins.storage import JSONLTapeStore
from coding_agent.providers.base import ToolSchema
from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONLRuntimeStore,
    JSONObject,
    PGRuntimeStore,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
    SQLiteRuntimeStore,
)
from coding_agent.stores import RuntimeStore
from coding_agent.topics.store import (
    PGTopicStore,
    SQLiteTopicStore,
    TopicAnchorRecord,
    TopicRecord,
)
from coding_agent.topics.semantic_maintenance import (
    SemanticMemoryMaintainer,
    SemanticMemoryStatus,
)
from coding_agent.topics.semantic_sync import SemanticSyncReport
from coding_agent.topics.lifecycle import TOPIC_FINALIZED, TOPIC_INITIAL
from coding_agent.topics.memory import (
    MemoryReviewStore,
    propose_memory_candidate_from_topic,
)
from coding_agent.executors import (
    LocalDaemonExecutor,
)
from coding_agent.events import DisplayEvent
from coding_agent.runs import (
    UNSET,
    UnsetType,
    DefaultRunCoordinator,
    EventBroadcastResult,
    LocalDaemonExecutorRef,
    RunCoordinator,
    RuntimeAgentFactoryService,
    RuntimeAttachedExecutorClaimService,
    RuntimeAttachedExecutorFinalizeService,
    RuntimeAttachedExecutorRequestService,
    RuntimeBindingSnapshot,
    RuntimeCancelObservationFinalizer,
    RuntimeCancelOrchestrationService,
    RuntimeCheckpointCaptureService,
    RuntimeCheckpointQueryService,
    RuntimeControlServices,
    RuntimeCloser,
    RuntimeContextBindingService,
    RuntimeEnsureOrchestrationService,
    RuntimeEnsureService,
    RuntimeMaintenanceAdmissionService,
    RuntimeRunMetadataService,
    RuntimeObservationService,
    RuntimePreparationRequestService,
    RuntimeReplacementService,
    RuntimeResumeContext as SessionResumeContext,
    RuntimeResumeOrchestrationService,
    RuntimeResumeSessionOrchestrationService,
    RuntimeTurnAdmissionService,
    RuntimeWorkspaceExportService,
    RuntimeWireEventRecorder,
    CloudWorkspaceRef,
    ExternalWorkerExecutorRef,
    IsolationPolicy,
    LocalAttachedExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
    SessionRuntimeHandle,
    run_target_from_legacy_session_payload,
    run_target_from_dict,
)
from coding_agent.runs.environment import RuntimeEnvironmentResolverService
from coding_agent.runs.runtime_preparation import LocalDaemonRuntimePreparationService
from coding_agent.runs.runtime_checkpoint_restore import (
    RuntimeCheckpointRestoreOrchestrationService,
)
from coding_agent.runs.runtime_checkpoint_restore import RuntimeCheckpointRestoreService
from coding_agent.runs.turn_service_factory import RuntimeTurnServiceFactory
from coding_agent.wire.consumer import LocalWireConsumer
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)
from coding_agent.server.stores.session_store import (
    SessionStore,
    create_session_store,
)
from coding_agent.server.stores.session_owner_store import OwnerAuthority
from coding_agent.server.stores.session_owner_store import SessionOwnerStoreProtocol
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictReason,
)
from coding_agent.environment.cloud import CloudWorkspaceClient
from coding_agent.server.stores.workspace_store import (
    JSONValue,
    WorkspaceRecord,
    WorkspaceRetentionPolicy,
    WorkspaceStatus,
)

if TYPE_CHECKING:
    from coding_agent.runs.turn_execution import RuntimeTurnService

logger = logging.getLogger(__name__)

GRACEFUL_SHUTDOWN_INTERRUPTED_RUN_ERROR = (
    "runtime run was interrupted during graceful shutdown"
)
GRACEFUL_SHUTDOWN_RECOVERY_REASON = "graceful_shutdown"
GRACEFUL_SHUTDOWN_INTERRUPTABLE_RUN_STATUSES = frozenset(
    {"running", "cancelling", "cancelled"}
)


def _custom_store_names(
    *,
    store: object | None,
    tape_store: object | None,
    checkpoint_store: object | None,
    checkpoint_service: object | None,
    runtime_store: object | None,
) -> list[str]:
    names: list[str] = []
    if store is not None:
        names.append("store")
    if tape_store is not None:
        names.append("tape_store")
    if checkpoint_store is not None:
        names.append("checkpoint_store")
    if checkpoint_service is not None:
        names.append("checkpoint_service")
    if runtime_store is not None:
        names.append("runtime_store")
    return names


T = TypeVar("T")


def _local_default_run_target(repo_path: Path | None) -> RunTarget:
    workspace_root = (
        str(repo_path.resolve()) if repo_path is not None else str(Path.cwd().resolve())
    )
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path=workspace_root),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


def _session_run_target(session: "Session") -> RunTarget:
    target = session.default_run_target
    if target is None:
        raise RuntimeError("session is missing default_run_target")
    return target


def _session_is_attached(session: "Session") -> bool:
    return isinstance(
        _session_run_target(session).executor,
        (ExternalWorkerExecutorRef, LocalAttachedExecutorRef),
    )


def _session_cloud_workspace(session: "Session") -> CloudWorkspaceRef | None:
    workspace = _session_run_target(session).workspace
    if isinstance(workspace, CloudWorkspaceRef):
        return workspace
    return None


def _subagent_message_id(session_id: str) -> str:
    return f"subagent_message:{session_id}:{uuid.uuid4().hex}"


TurnStatus = Literal["idle", "running", "cancelling", "cancelled", "failed"]
CancelTurnStatus = Literal["idle", "cancelling", "cancelled", "failed"]
_ACTIVE_RESUME_BLOCKING_RUN_STATUSES = {
    "queued",
    "requested",
    "claimed",
    "running",
    "cancelling",
}
_SEMANTIC_MEMORY_REBUILD_MAX_BATCH_SIZE = 1000


def _runtime_memory_write_enabled(
    config: Mapping[str, object],
    *,
    review_store: MemoryReviewStore,
) -> bool:
    memory_config = config.get("memory")
    if memory_config is None:
        return review_store.candidate_writes_enabled
    if not isinstance(memory_config, Mapping):
        raise RuntimeError("Session runtime memory config is invalid")
    value = memory_config.get("effective_write_enabled")
    if not isinstance(value, bool):
        raise RuntimeError(
            "Session runtime memory config is missing effective_write_enabled"
        )
    return value


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


@dataclass(frozen=True)
class SessionRecord:
    """Durable session metadata stored across process restarts."""

    id: str
    created_at: datetime
    last_activity: datetime
    repo_path: Path | None
    origin: dict[str, str] | None
    default_run_target: RunTarget
    approval_policy: ApprovalPolicy
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    max_steps: int
    mcp_servers: dict[str, dict[str, Any]]
    additional_directories: list[str]
    tape_id: str | None
    last_failure_details: str | None

    def to_store_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            # repo_path remains backward-compatible metadata and seeds the
            # default local RunTarget when placement metadata is omitted.
            "repo_path": None if self.repo_path is None else str(self.repo_path),
            "origin": None if self.origin is None else dict(self.origin),
            "default_run_target": self.default_run_target.to_dict(),
            "approval_policy": self.approval_policy.value,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
            "mcp_servers": dict(self.mcp_servers),
            "additional_directories": list(self.additional_directories),
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
        mcp_servers_raw = data.get("mcp_servers", {})
        if not isinstance(mcp_servers_raw, dict):
            raise TypeError("session metadata has invalid mcp_servers")
        mcp_servers = _session_mcp_servers_from_store(
            cast(dict[str, Any], mcp_servers_raw)
        )
        additional_directories = _session_additional_directories_from_store(
            data.get("additional_directories", [])
        )
        default_run_target_raw = data.get("default_run_target")
        if default_run_target_raw is None:
            legacy_target_raw = data.get("execution_binding")
            if legacy_target_raw is not None:
                if not isinstance(legacy_target_raw, dict):
                    raise TypeError("session metadata has invalid legacy run target")
                default_run_target = run_target_from_legacy_session_payload(
                    cast(dict[str, object], legacy_target_raw)
                )
            else:
                default_run_target = _local_default_run_target(
                    None if repo_path_raw is None else Path(repo_path_raw)
                )
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
            default_run_target=default_run_target,
            approval_policy=ApprovalPolicy(approval_policy_raw),
            provider_name=provider_name_raw,
            model_name=model_name_raw,
            base_url=base_url_raw,
            max_steps=_required_session_int(data, "max_steps"),
            mcp_servers=mcp_servers,
            additional_directories=additional_directories,
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
            default_run_target=self.default_run_target,
            approval_policy=self.approval_policy,
            provider_name=self.provider_name,
            model_name=self.model_name,
            base_url=self.base_url,
            max_steps=self.max_steps,
            mcp_servers=dict(self.mcp_servers),
            additional_directories=list(self.additional_directories),
            tape_id=self.tape_id,
            last_failure_details=self.last_failure_details,
        )


@dataclass
class Session:
    """A managed agent session.

    ``default_run_target`` is the canonical run placement contract.
    """

    id: str
    created_at: datetime
    last_activity: datetime
    wire: LocalWire = field(init=False)
    approval_store: ApprovalStore = field(default_factory=ApprovalStore)
    repo_path: Path | None = None  # legacy metadata; seeds default local target
    origin: dict[str, str] | None = None
    default_run_target: RunTarget | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: Any | None = None
    provider_name: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    thinking_config: dict[str, Any] = field(
        default_factory=lambda: {"enabled": True, "effort": "medium"}
    )
    max_steps: int = 30
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    additional_directories: list[str] = field(default_factory=list)
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
        if name == "execution_binding":
            raise AttributeError(
                "Session.execution_binding was removed; use default_run_target"
            )
        if name == "approval_store":
            object.__setattr__(self, name, value)
            instance_dict = object.__getattribute__(self, "__dict__")
            handle = instance_dict.get("runtime_handle")
            if handle is not None:
                handle.approval_coordinator = ApprovalCoordinator(
                    cast(ApprovalStore, value)
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
        if self.default_run_target is None:
            object.__setattr__(
                self,
                "default_run_target",
                _local_default_run_target(self.repo_path),
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
        return self.runtime_handle.broadcast_event_nowait(event)

    def clear_approval_runtime_state(self) -> None:
        self.runtime_handle.clear_approval_runtime_state()

    def begin_approval_request(self, request: ApprovalRequest) -> None:
        self.runtime_handle.begin_approval_request(request)

    def update_pending_approval_projection(
        self,
        *,
        signal_event: bool = False,
    ) -> None:
        self.runtime_handle.update_pending_approval_projection(
            signal_event=signal_event,
        )

    def expose_approval_response(self, response_projection: dict[str, Any]) -> None:
        self.runtime_handle.expose_approval_response(response_projection)

    def cleanup_approval_wait_projection(self, *, signal_event: bool) -> None:
        self.runtime_handle.cleanup_approval_wait_projection(
            signal_event=signal_event,
        )

    def detach_runtime_adapter(self) -> object | None:
        return self.runtime_handle.detach_runtime_adapter()

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None:
        self.runtime_handle.attach_runtime_binding(
            pipeline=pipeline,
            ctx=ctx,
            adapter=adapter,
        )

    def runtime_binding_snapshot(self) -> RuntimeBindingSnapshot:
        return self.runtime_handle.runtime_binding_snapshot()

    def restore_runtime_binding(self, snapshot: RuntimeBindingSnapshot) -> None:
        self.runtime_handle.restore_runtime_binding(snapshot)

    def as_dict(self) -> dict[str, Any]:
        target = self.default_run_target
        if target is None:
            raise RuntimeError("session is missing default_run_target")
        workspace = target.workspace
        workspace_id = (
            workspace.workspace_id if isinstance(workspace, CloudWorkspaceRef) else None
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
            "default_run_target": target.to_dict(),
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
            default_run_target=self.default_run_target,
            approval_policy=self.approval_policy,
            provider_name=self.provider_name,
            model_name=self.model_name,
            base_url=self.base_url,
            max_steps=self.max_steps,
            mcp_servers=dict(self.mcp_servers),
            additional_directories=list(self.additional_directories),
            tape_id=self.tape_id,
            last_failure_details=self.last_failure_details,
        )

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> Session:
        session = SessionRecord.from_store_data(data).to_session()
        session.turn_in_progress = False
        session.clear_approval_runtime_state()
        return session


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


def _session_mcp_servers_from_store(
    servers: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for name, raw in servers.items():
        if not isinstance(name, str) or not name:
            raise TypeError("session metadata has invalid mcp server name")
        if not isinstance(raw, dict):
            raise TypeError(f"session metadata has invalid mcp server: {name}")
        command = raw.get("command")
        if not isinstance(command, str) or not command:
            raise TypeError(f"session metadata has invalid mcp command: {name}")
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise TypeError(f"session metadata has invalid mcp args: {name}")
        env = raw.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in env.items()
        ):
            raise TypeError(f"session metadata has invalid mcp env: {name}")
        inherit_env = raw.get("inherit_env", False)
        if not isinstance(inherit_env, bool):
            raise TypeError(f"session metadata has invalid mcp inherit_env: {name}")
        normalized[name] = {
            "command": command,
            "args": list(args),
            "env": dict(env),
            "inherit_env": inherit_env,
        }
    return normalized


def _session_additional_directories_from_store(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("session metadata has invalid additional_directories")
    normalized: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry:
            raise TypeError("session metadata has invalid additional_directories")
        path = Path(entry).expanduser()
        if not path.is_absolute():
            raise TypeError("session metadata has invalid additional_directories")
        normalized.append(str(path.resolve()))
    return normalized


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


@dataclass(frozen=True, slots=True)
class SemanticDogfoodTopicSeedResult:
    topic_id: str
    candidate_id: str | None
    warnings: tuple[str, ...] = ()


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
        cloud_workspace_client_factory: Callable[
            [CloudWorkspaceRef], CloudWorkspaceClient
        ]
        | None = None,
        provisioned_cloud_binding_cleanup: (
            Callable[[CloudWorkspaceRef], None] | None
        ) = None,
        workspace_metadata_store: WorkspaceMetadataStoreProtocol | None = None,
        runtime_store: RuntimeStore | None = None,
        observation_store: AgentObservationStore | None = None,
        owner_store: SessionOwnerStoreProtocol | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
        owner_lease_seconds: float = 30.0,
        run_coordinator: RunCoordinator | None = None,
        local_daemon_executor: LocalDaemonExecutor | None = None,
    ):
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        self._storage_config = (
            with_local_sqlite_bundle_paths(dict(storage_config), data_dir)
            if storage_config
            else local_sqlite_storage_config(data_dir)
        )
        self._local_sqlite_bundle_path = normalize_storage_path(
            str(local_sqlite_path_from_storage_config(self._storage_config, data_dir))
        )
        self._pg_pool = pg_pool
        self._owns_pg_pool = False
        self._custom_store_names = _custom_store_names(
            store=store,
            tape_store=tape_store,
            checkpoint_store=checkpoint_store,
            checkpoint_service=checkpoint_service,
            runtime_store=runtime_store,
        )
        self._local_durable_store = self._create_local_durable_store(
            owner_store=owner_store,
        )
        self._pg_durable_store: PGDurableStore | None = None
        self._store = store or self._create_http_session_store()
        self._session_cache: dict[str, Session] = {}
        self._approval_stores: dict[str, ApprovalStore] = {}
        self._lock = asyncio.Lock()
        self._store_io_guard = threading.Lock()
        self._session_turn_locks: dict[str, asyncio.Lock] = {}
        self._session_workspace_export_counts: dict[str, int] = {}
        self._tape_store = tape_store or self._create_tape_store(data_dir)
        if self._local_durable_store is not None and tape_store is None:
            self._tape_store = FencedSQLiteTapeStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
            )
        self._agent_observation_store = observation_store or JsonlAgentObservationStore(
            data_dir / "observability"
        )
        self._runtime_observation_service = RuntimeObservationService(
            self._agent_observation_store
        )
        self._runtime_metadata_service = RuntimeRunMetadataService()
        resolved_checkpoint_store = checkpoint_store or self._create_checkpoint_store(
            data_dir
        )
        if (
            self._local_durable_store is not None
            and checkpoint_store is None
            and checkpoint_service is None
        ):
            resolved_checkpoint_store = FencedSQLiteCheckpointStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
            )
        self._checkpoint_service = checkpoint_service or CheckpointService(
            resolved_checkpoint_store
        )
        self._create_agent = create_agent_fn
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
        if self._local_durable_store is not None and runtime_store is None:
            self._runtime_store = FencedSQLiteRuntimeStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
                authorities=lambda: dict(self._owner_authorities),
            )

        async def runtime_session_is_recoverable(session_id: str) -> bool:
            if self._owner_store is None:
                return True
            return await self._holds_active_owner_lease(session_id)

        self._runtime_control_services = RuntimeControlServices(
            store=lambda: self._runtime_store,
            metadata_for_session=self._runtime_metadata_service.metadata_for_session,
            list_session_ids=self.list_sessions_async,
            session_is_recoverable=runtime_session_is_recoverable,
            owner_id=lambda: self._owner_id,
            active_resume_blocking_statuses=frozenset(
                _ACTIVE_RESUME_BLOCKING_RUN_STATUSES
            ),
        )
        self._runtime_cancel_orchestration = RuntimeCancelOrchestrationService(
            cancel_service=self._runtime_control_services.cancel,
            persist_session=self._persist_session_async,
            session_is_attached=lambda session: _session_is_attached(
                cast(Session, session)
            ),
            schedule_cancel_observation=self._schedule_cancel_observation,
            turn_id_factory=lambda: uuid.uuid4().hex,
        )
        self._runtime_cancel_observation_finalizer = RuntimeCancelObservationFinalizer(
            cancel_service=self._runtime_control_services.cancel,
            load_session=self.get_session_async,
            persist_session=self._persist_session_async,
            session_has_task=lambda session, task: session.task is task,
            lock=lambda: self._lock,
        )
        self._runtime_turn_admission = RuntimeTurnAdmissionService(
            turn_lock_for=self._turn_lock_for,
            workspace_export_in_progress=self._workspace_export_in_progress,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
        )
        self._runtime_workspace_export_service = RuntimeWorkspaceExportService(
            turn_lock_for=self._turn_lock_for,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
            begin_export=self._begin_workspace_export,
            end_export=self._end_workspace_export,
        )
        self._runtime_maintenance_admission = RuntimeMaintenanceAdmissionService(
            turn_lock_for=self._turn_lock_for,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
        )
        self._runtime_closer = RuntimeCloser()
        self._runtime_agent_factory_service = RuntimeAgentFactoryService(
            create_agent=self._create_agent,
        )
        self._runtime_preparation_request_service = RuntimePreparationRequestService()
        self._runtime_replacement_service = RuntimeReplacementService(
            close_runtime_adapter=self._runtime_closer.close_adapter,
        )
        self._runtime_ensure_service = RuntimeEnsureService()
        self._runtime_ensure_orchestration = RuntimeEnsureOrchestrationService(
            ensure_service=self._runtime_ensure_service,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
            build_runtime=lambda session: self._build_session_runtime(
                cast(Session, session)
            ),
            persist_session=lambda session: self._persist_session_async(
                cast(Session, session)
            ),
        )
        self._runtime_environment_resolver_service = RuntimeEnvironmentResolverService(
            cloud_client_factory=cloud_workspace_client_factory
        )
        self._runtime_context_binding_service = RuntimeContextBindingService(
            publish_subagent_message=self.publish_subagent_message,
        )
        self._local_daemon_runtime_preparation = LocalDaemonRuntimePreparationService(
            environment_resolver=self._runtime_environment_resolver_service,
            local_daemon_executor=self._local_daemon_executor,
            close_runtime=self._runtime_closer.close,
            close_runtime_adapter=self._runtime_closer.close_adapter,
            create_agent_for_session=(
                self._runtime_agent_factory_service.create_agent_for_session
            ),
            restore_tape=self._restore_tape,
            persist_session=self._persist_session_async,
            make_consumer=self._make_session_consumer,
            bind_subagent_message_publisher=(
                self._runtime_context_binding_service.bind_subagent_message_publisher
            ),
            runtime_preparation_request=(
                self._runtime_preparation_request_service.request_for_session
            ),
            semantic_topic_store_factory=self.selected_topic_store,
            adapter_factory=lambda pipeline, ctx, consumer: PipelineAdapter(
                pipeline=pipeline,
                ctx=ctx,
                consumer=consumer,
            ),
        )
        self._runtime_checkpoint_restore_service = RuntimeCheckpointRestoreService(
            checkpoint_service=lambda: self._checkpoint_service,
            tape_store=lambda: self._tape_store,
            local_daemon_executor=self._local_daemon_executor,
            resolve_environment_for_run_target=(
                self._runtime_environment_resolver_service.resolve_environment_for_run_target
            ),
            workspace_root_for_environment=(
                self._runtime_environment_resolver_service.workspace_root_for_environment
            ),
            create_agent_for_session=(
                self._runtime_agent_factory_service.create_agent_for_session
            ),
            bind_subagent_message_publisher=(
                self._runtime_context_binding_service.bind_subagent_message_publisher
            ),
            restore_consumer_factory=self._make_restore_consumer,
            adapter_factory=lambda pipeline, ctx, consumer: PipelineAdapter(
                pipeline=pipeline,
                ctx=ctx,
                consumer=consumer,
            ),
            runtime_preparation_request=(
                self._runtime_preparation_request_service.request_for_session
            ),
            close_runtime=self._runtime_closer.close,
            persist_session=self._persist_session_async,
            semantic_topic_store_factory=self.selected_topic_store,
            restore_durable_state=self._restore_checkpoint_durable_state,
        )
        self._runtime_checkpoint_restore_orchestration = (
            RuntimeCheckpointRestoreOrchestrationService(
                admission=self._runtime_maintenance_admission,
                restore=lambda session, checkpoint_id: (
                    self._runtime_checkpoint_restore_service.restore(
                        session,
                        checkpoint_id,
                    )
                ),
            )
        )
        self._runtime_checkpoint_query_service = RuntimeCheckpointQueryService(
            checkpoint_service=lambda: self._checkpoint_service,
        )
        self._runtime_checkpoint_capture_service = RuntimeCheckpointCaptureService(
            checkpoint_service=lambda: self._checkpoint_service,
            ensure_runtime=lambda session_id: self.ensure_session_runtime(session_id),
            persist_session=lambda session: self._persist_session_async(session),
        )
        self._runtime_resume_orchestration = RuntimeResumeOrchestrationService(
            resume_service=self._runtime_control_services.resume(),
            latest_runtime_run=lambda session_id: (
                self._runtime_control_services.queries().latest_runtime_run(session_id)
            ),
            latest_runtime_event_id=lambda run: (
                self._runtime_control_services.queries().latest_runtime_event_id(run)
            ),
            load_runtime_run=lambda run_id: (
                self._require_runtime_store().load_agent_run(run_id)
            ),
            persist_session=lambda session: self._persist_session_async(session),
            list_checkpoints=self.list_checkpoints,
            load_tape_entries=self._tape_store.load,
            save_tape_entries=self._tape_store.save,
            load_message_snapshot=lambda snapshot_id: (
                self._require_runtime_store().load_message_snapshot(snapshot_id)
            ),
            run_local=self._run_resumed_local_session,
            request_attached=self._request_resumed_attached_executor_run,
            session_is_attached=lambda session: _session_is_attached(
                cast(Session, session)
            ),
            append_live_boundary_anchor=self._append_live_resume_boundary_anchor,
            active_resume_blocking_statuses=frozenset(
                _ACTIVE_RESUME_BLOCKING_RUN_STATUSES
            ),
        )
        self._runtime_resume_session_orchestration = (
            RuntimeResumeSessionOrchestrationService(
                require_runtime_store=self._require_runtime_store,
                assert_owner=self._assert_owner,
                load_session=self.get_session_async,
                resume_orchestration=self._runtime_resume_orchestration,
            )
        )
        self._runtime_attached_executor_request_service = (
            RuntimeAttachedExecutorRequestService(
                lock=self._lock,
                assert_owner=self._assert_owner,
                load_session=self.get_session_async,
                attached_executor=self._runtime_control_services.attached_executor,
                persist_session=self._persist_session_async,
                session_is_attached=lambda session: _session_is_attached(
                    cast(Session, session)
                ),
            )
        )
        self._runtime_attached_executor_claim_service = (
            RuntimeAttachedExecutorClaimService(
                attached_executor=self._runtime_control_services.attached_executor,
                load_session=self.get_session_async,
                claim_factory=lambda claim, session: ExternalWorkerClaim(
                    run=claim.run,
                    claim_token=claim.claim_token,
                    prompt=claim.prompt,
                    session=cast(Session, session),
                ),
            )
        )
        self._runtime_attached_executor_finalize_service = (
            RuntimeAttachedExecutorFinalizeService(
                lock=self._lock,
                load_session=self.get_session_async,
                attached_executor=self._runtime_control_services.attached_executor,
                save_tape_entries=lambda tape_id, entries: self._tape_store.save(
                    tape_id,
                    entries,
                ),
                persist_session=self._persist_session_async,
            )
        )
        self._runtime_turn_service_factory = RuntimeTurnServiceFactory(
            runtime_control_services=self._runtime_control_services,
            persist_session=self._persist_session_async,
            make_consumer=self._make_session_consumer,
            prepare_runtime=self._local_daemon_runtime_preparation.prepare_runtime,
            close_runtime=self._runtime_closer.close,
            emit_message=self._send_session_wire_message,
            bind_root_run_identity=(
                self._runtime_context_binding_service.bind_root_run_identity
            ),
            bind_subagent_message_publisher=(
                self._runtime_context_binding_service.bind_subagent_message_publisher
            ),
            start_observation=self._runtime_observation_service.start,
            complete_observation=self._runtime_observation_service.complete,
            log_turn_exception=lambda message: logger.exception(message),
        )
        self._runtime_turn_service = self._build_runtime_turn_service()
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

    def selected_topic_store(self) -> SQLiteTopicStore | PGTopicStore | None:
        if self._local_durable_store is not None:
            if not storage_uses_local_sqlite_bundle(self._storage_config):
                return None
            return FencedSQLiteTopicStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
            )
        backend_values = durable_storage_backend_values(self._storage_config)
        if all(value == "pg" for value in backend_values.values()):
            if self._custom_store_names:
                return None
            if self._pg_durable_store is None:
                return PGTopicStore(pool=self._get_pg_pool())
            return FencedPGTopicStore(
                durable_store=self._pg_durable_store,
                pool=self._get_pg_pool(),
                authority_for_session=self._owner_authority_for_session,
            )
        return None

    def _sqlite_storage_path(self, path_key: str, default: Path) -> Path:
        path_obj = self._storage_config.get(path_key)
        if isinstance(path_obj, str) and path_obj.strip():
            return normalize_storage_path(path_obj)
        return default

    async def semantic_memory_maintainer(
        self,
        session_id: str,
    ) -> SemanticMemoryMaintainer:
        runtime_ctx = await self.ensure_session_runtime(session_id)
        config = getattr(runtime_ctx, "config", None)
        if not isinstance(config, dict):
            raise RuntimeError("semantic memory is disabled")
        backend = config.get("semantic_memory_backend")
        syncer = config.get("semantic_memory_syncer")
        review_store = config.get("memory_review_store")
        if backend is None or syncer is None or review_store is None:
            raise RuntimeError("semantic memory is disabled")
        return SemanticMemoryMaintainer(
            syncer=syncer,
            backend=backend,
            review_store=review_store,
            topic_store=self.selected_topic_store(),
        )

    async def semantic_memory_status(self, session_id: str) -> SemanticMemoryStatus:
        maintainer = await self.semantic_memory_maintainer(session_id)
        return await maintainer.status()

    async def rebuild_semantic_memory(
        self,
        session_id: str,
        *,
        batch_size: int,
        allow_rebuild: bool,
    ) -> SemanticSyncReport:
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size <= 0
            or batch_size > _SEMANTIC_MEMORY_REBUILD_MAX_BATCH_SIZE
        ):
            raise ValueError("batch_size must be between 1 and 1000")
        if not isinstance(allow_rebuild, bool):
            raise ValueError("allow_rebuild must be a boolean")

        async def rebuild_admitted_semantic_memory(
            session: object,
        ) -> SemanticSyncReport:
            del session
            maintainer = await self.semantic_memory_maintainer(session_id)
            return await maintainer.rebuild(
                batch_size=batch_size,
                allow_rebuild=allow_rebuild,
            )

        return cast(
            SemanticSyncReport,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                rebuild_admitted_semantic_memory,
            ),
        )

    async def seed_semantic_dogfood_topic(
        self,
        session_id: str,
        *,
        title: str,
        summary: str,
        kind: str = "coding",
    ) -> SemanticDogfoodTopicSeedResult:
        if not title.strip():
            raise ValueError("title must not be blank")
        if not summary.strip():
            raise ValueError("summary must not be blank")
        if not kind.strip():
            raise ValueError("kind must not be blank")

        async def seed_admitted_semantic_dogfood_topic(
            session: object,
        ) -> SemanticDogfoodTopicSeedResult:
            del session
            runtime_ctx = await self.ensure_session_runtime(session_id)
            runtime_tape = getattr(runtime_ctx, "tape", None)
            if not isinstance(runtime_tape, Tape):
                raise RuntimeError("Session runtime context is missing tape")
            config = getattr(runtime_ctx, "config", None)
            if not isinstance(config, Mapping):
                raise RuntimeError("Session runtime context is missing config")
            topic_store = self.selected_topic_store()
            if topic_store is None:
                raise RuntimeError(
                    "topic_store is required for semantic dogfood topic seed"
                )
            review_store = config.get("memory_review_store")
            if not isinstance(review_store, MemoryReviewStore):
                raise RuntimeError("memory_review_store is required for dogfood topic")
            semantic_syncer = config.get("semantic_memory_syncer")
            if semantic_syncer is not None and not callable(
                getattr(semantic_syncer, "sync_topic", None)
            ):
                raise RuntimeError("semantic_memory_syncer is configured incorrectly")
            memory_write_enabled = _runtime_memory_write_enabled(
                config,
                review_store=review_store,
            )
            fork_store = ForkTapeStore(self._tape_store)
            fork = fork_store.begin(runtime_tape)
            base_len = len(runtime_tape)
            stable_tape_id = runtime_tape.tape_id
            topic_id = f"topic-{uuid.uuid4().hex}"
            title_value = title.strip()
            summary_value = summary.strip()
            kind_value = kind.strip()
            initial_anchor = Anchor(
                anchor_type="topic_start",
                payload={"label": title_value},
                meta={
                    "topic_id": topic_id,
                    "product_anchor_type": TOPIC_INITIAL,
                    "skip": True,
                },
            )
            initial_seq = len(fork)
            fork.append(initial_anchor)
            finalized_anchor = Anchor(
                anchor_type="topic_end",
                payload={"label": summary_value},
                meta={
                    "topic_id": topic_id,
                    "product_anchor_type": TOPIC_FINALIZED,
                    "skip": True,
                },
            )
            finalized_seq = len(fork)
            fork.append(finalized_anchor)
            try:
                stable_tape_id = await fork_store.commit(fork)
            except Exception:
                fork_store.rollback(fork)
                raise
            created_at = datetime.now(UTC)
            finalized_at = created_at
            finalized: TopicRecord | None = None
            stored_candidate_id: str | None = None
            warnings: list[str] = []
            topic_created = False
            try:
                topic = await topic_store.create_topic(
                    TopicRecord(
                        topic_id=topic_id,
                        tape_id=stable_tape_id,
                        session_id=session_id,
                        kind=kind_value,
                        status="open",
                        title=title_value,
                        summary=None,
                        owner="semantic-dogfood",
                        topic_initial_seq=initial_seq,
                        topic_finalized_seq=None,
                        created_at=created_at,
                        finalized_at=None,
                        metadata={"source": "semantic-dogfood-topic"},
                    )
                )
                topic_created = True
                await topic_store.record_topic_anchor(
                    TopicAnchorRecord(
                        topic_id=topic.topic_id,
                        tape_id=stable_tape_id,
                        seq=initial_seq,
                        anchor_type=TOPIC_INITIAL,
                        entry_id=initial_anchor.id,
                        metadata={
                            "encoded_anchor_type": "topic_start",
                            "product_anchor_type": TOPIC_INITIAL,
                        },
                    )
                )
                finalized = await topic_store.finalize_topic(
                    topic.topic_id,
                    summary=summary_value,
                    topic_finalized_seq=finalized_seq,
                    finalized_at=finalized_at,
                    metadata={"source": "semantic-dogfood-topic"},
                )
                await topic_store.record_topic_anchor(
                    TopicAnchorRecord(
                        topic_id=finalized.topic_id,
                        tape_id=stable_tape_id,
                        seq=finalized_seq,
                        anchor_type=TOPIC_FINALIZED,
                        entry_id=finalized_anchor.id,
                        metadata={
                            "encoded_anchor_type": "topic_end",
                            "product_anchor_type": TOPIC_FINALIZED,
                        },
                    )
                )
            except Exception as exc:
                delete_topic = getattr(topic_store, "delete_topic", None)
                if topic_created:
                    if delete_topic is None or not callable(delete_topic):
                        raise RuntimeError(
                            "semantic dogfood topic seed failed after tape commit "
                            "and topic compensation is unavailable"
                        ) from exc
                    try:
                        await delete_topic(topic_id)
                    except Exception as compensation_exc:
                        raise RuntimeError(
                            "semantic dogfood topic seed failed after tape commit "
                            "and topic compensation failed: "
                            f"{exc}; compensation error: {compensation_exc}"
                        ) from exc
                try:
                    await self._tape_store.truncate(stable_tape_id, base_len)
                except Exception as compensation_exc:
                    raise RuntimeError(
                        "semantic dogfood topic seed failed after tape commit "
                        "and tape compensation failed: "
                        f"{exc}; compensation error: {compensation_exc}"
                    ) from exc
                raise
            if finalized is None:
                raise RuntimeError("semantic dogfood topic seed did not finalize topic")
            if memory_write_enabled and review_store.candidate_writes_enabled:
                candidate = propose_memory_candidate_from_topic(finalized)
                if candidate is not None:
                    try:
                        stored_candidate = review_store.add_candidate(candidate)
                    except Exception as exc:
                        warning = f"memory review candidate write failed: {exc}"
                        logger.warning(
                            "Semantic dogfood topic review candidate write failed",
                            exc_info=True,
                        )
                        warnings.append(warning)
                    else:
                        stored_candidate_id = stored_candidate.candidate.candidate_id
            if semantic_syncer is not None:
                try:
                    await semantic_syncer.sync_topic(finalized)
                except Exception as exc:
                    warning = f"semantic topic sync failed: {exc}"
                    logger.warning(
                        "Semantic dogfood topic sync failed",
                        exc_info=True,
                    )
                    warnings.append(warning)
            fork.tape_id = stable_tape_id
            runtime_ctx.tape = fork
            return SemanticDogfoodTopicSeedResult(
                topic_id=finalized.topic_id,
                candidate_id=stored_candidate_id,
                warnings=tuple(warnings),
            )

        return cast(
            SemanticDogfoodTopicSeedResult,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                seed_admitted_semantic_dogfood_topic,
            ),
        )

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
        self._owner_authorities: dict[str, OwnerAuthority] = {}
        self._configure_pg_durable_store_if_available()

    def _create_local_durable_store(
        self,
        *,
        owner_store: SessionOwnerStoreProtocol | None,
    ) -> SQLiteLocalDurableStore | None:
        if owner_store is None or not callable(
            getattr(owner_store, "acquire_authority", None)
        ):
            return None
        config = self._storage_config
        backend_values = durable_storage_backend_values(config)
        if all(value == "pg" for value in backend_values.values()):
            return None
        sqlite_backend_keys = [
            key for key, value in backend_values.items() if value == "sqlite"
        ]
        if sqlite_backend_keys and len(sqlite_backend_keys) != len(
            DURABLE_STORAGE_BACKEND_KEYS
        ):
            mismatches = ", ".join(
                f"{key}={config.get(key)!r}"
                for key, value in backend_values.items()
                if value != "sqlite"
            )
            raise ConfigError(
                "durable fencing requires all local sqlite backends when any "
                f"local sqlite backend is configured; mismatched backends: {mismatches}"
            )
        if not sqlite_backend_keys:
            return None
        # Custom stores own their path semantics; validate sqlite paths only when
        # SessionManager will create the local durable bundle itself.
        if self._custom_store_names:
            logger.warning(
                "durable fencing disabled: custom %s supplied",
                ", ".join(self._custom_store_names),
            )
            return None
        local_path = self._local_sqlite_bundle_path
        configured_paths = {
            key: normalize_storage_path(str(config.get(key, "")))
            for key in DURABLE_STORAGE_PATH_KEYS
        }
        path_mismatches = [
            f"{key}={config.get(key)!r}"
            for key, path in configured_paths.items()
            if path != local_path
        ]
        if path_mismatches:
            mismatch_text = ", ".join(path_mismatches)
            raise ConfigError(
                "durable fencing requires sqlite storage paths to share "
                f"{local_path}; mismatched paths: {mismatch_text}"
            )
        return SQLiteLocalDurableStore(local_path)

    def _configure_pg_durable_store_if_available(self) -> None:
        if self._pg_durable_store is not None:
            return
        if self._local_durable_store is not None:
            return
        if self._owner_store is None or not callable(
            getattr(self._owner_store, "acquire_authority", None)
        ):
            return
        if any(
            str(self._storage_config.get(key, "")).strip().lower() != "pg"
            for key in DURABLE_STORAGE_BACKEND_KEYS
        ):
            return
        if self._custom_store_names:
            logger.warning(
                "durable fencing disabled: custom %s supplied",
                ", ".join(self._custom_store_names),
            )
            return
        pg_pool = self._get_pg_pool()
        durable_store = PGDurableStore(pool=pg_pool)
        self._pg_durable_store = durable_store
        self._tape_store = FencedPGTapeStore(
            durable_store=durable_store,
            pool=pg_pool,
            authority_for_session=self._owner_authority_for_session,
        )
        self._checkpoint_service = CheckpointService(
            FencedPGCheckpointStore(
                durable_store=durable_store,
                pool=pg_pool,
                authority_for_session=self._owner_authority_for_session,
            )
        )
        self._runtime_store = FencedPGRuntimeStore(
            durable_store=durable_store,
            pool=pg_pool,
            authority_for_session=self._owner_authority_for_session,
            authorities=lambda: dict(self._owner_authorities),
        )

    def configure_workspace_metadata_store(
        self,
        workspace_metadata_store: WorkspaceMetadataStoreProtocol | None,
    ) -> None:
        self._workspace_metadata_store = workspace_metadata_store

    def configure_runtime_store(
        self,
        runtime_store: RuntimeStore | None,
    ) -> None:
        self._runtime_store = runtime_store
        self._runtime_turn_service = self._build_runtime_turn_service()

    def configure_run_coordinator(self, run_coordinator: RunCoordinator) -> None:
        self._run_coordinator = run_coordinator
        self._runtime_turn_service = self._build_runtime_turn_service()

    def _build_runtime_turn_service(self) -> RuntimeTurnService:
        return self._runtime_turn_service_factory.build(self._run_coordinator)

    def _require_runtime_store(self) -> RuntimeStore:
        if self._runtime_store is None:
            raise RuntimeError("runtime store is not configured")
        return self._runtime_store

    async def load_runtime_run(self, run_id: str) -> AgentRunRecord:
        return await self._runtime_control_services.queries().load_runtime_run(run_id)

    async def list_runtime_runs(self, session_id: str) -> list[AgentRunRecord]:
        return await self._runtime_control_services.queries().list_runtime_runs(
            session_id
        )

    async def session_resume_metadata(self, session_id: str) -> dict[str, Any]:
        session = await self.get_session_async(session_id)
        return await self._runtime_control_services.queries().session_resume_metadata(
            session,
            list_checkpoints=self.list_checkpoints,
        )

    async def list_runtime_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return await self._runtime_control_services.queries().list_runtime_interactions(
            run_id
        )

    async def load_runtime_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord:
        return await self._runtime_control_services.queries().load_runtime_interaction(
            interaction_id
        )

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
        return await self._runtime_control_services.queries().load_runtime_message_snapshot(
            run_id
        )

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        return await self._runtime_control_services.queries().replay_runtime_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )

    async def replay_display_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[DisplayEvent]:
        return await self._runtime_control_services.queries().replay_display_events(
            run_id,
            last_event_id=last_event_id,
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
        return await self._runtime_attached_executor_request_service.request_run(
            session_id,
            prompt,
            run_id=run_id,
            resume_context=resume_context,
        )

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
        return await self._runtime_resume_session_orchestration.resume_session(
            session_id,
            prompt=prompt,
            resume_reason=resume_reason,
        )

    async def _run_resumed_local_session(
        self,
        session_id: str,
        prompt: str,
        run_id: str,
        resume_context: SessionResumeContext,
    ) -> AgentRunRecord | None:
        await self.run_agent(
            session_id,
            prompt,
            run_id_override=run_id,
            resume_context=resume_context,
        )
        return None

    async def _request_resumed_attached_executor_run(
        self,
        session_id: str,
        prompt: str,
        run_id: str,
        resume_context: SessionResumeContext,
    ) -> AgentRunRecord:
        return await self.request_attached_executor_run(
            session_id,
            prompt,
            run_id=run_id,
            resume_context=resume_context,
        )

    def _append_live_resume_boundary_anchor(
        self, session: Session, anchor: Anchor
    ) -> None:
        runtime_ctx = session.runtime_ctx
        tape = getattr(runtime_ctx, "tape", None)
        if isinstance(tape, Tape) and tape.tape_id == session.tape_id:
            tape.append(anchor)

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
        claim = await self._runtime_attached_executor_claim_service.claim_run(
            executor_id=executor_id,
            executor_kind=executor_kind,
            session_id=session_id,
            lease_seconds=lease_seconds,
            worker_instance_id=worker_instance_id,
            process_id=process_id,
            capabilities=capabilities,
            workspace_sync=workspace_sync,
        )
        return cast(ExternalWorkerClaim | None, claim)

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
        return await self._runtime_control_services.attached_executor().heartbeat_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
            lease_seconds=lease_seconds,
            worker_instance_id=worker_instance_id,
            process_id=process_id,
            capabilities=capabilities,
            workspace_sync=workspace_sync,
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
        return await self._runtime_control_services.attached_executor().append_event(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
            event_id=event_id,
            event_kind=event_kind,
            payload=payload,
            created_at=created_at,
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
        return await self._runtime_attached_executor_finalize_service.finalize_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
            status=status,
            result=result,
            error=error,
            tape_id=tape_id,
            tape_entries=tape_entries,
        )

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
        if backend == "sqlite":
            session_path = str(
                self._sqlite_storage_path(
                    "http_session_path",
                    self._local_sqlite_bundle_path,
                )
            )
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
            path = self._sqlite_storage_path(
                "tape_path", self._local_sqlite_bundle_path
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
            path = self._sqlite_storage_path(
                "checkpoint_path",
                self._local_sqlite_bundle_path,
            )
            return SQLiteCheckpointStore(path)
        return FSCheckpointStore(data_dir / "checkpoints")

    def _create_runtime_store(self) -> RuntimeStore | None:
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
            path = self._sqlite_storage_path(
                "runtime_path",
                self._local_sqlite_bundle_path,
            )
            return SQLiteRuntimeStore(path)
        raise ValueError(f"unsupported storage.runtime_backend: {backend}")

    def _session_uses_provisioned_cloud_workspace(self, session: Session) -> bool:
        origin = session.origin
        return (
            _session_cloud_workspace(session) is not None
            and origin is not None
            and origin.get("placement_kind") == "cloud_workspace"
            and origin.get("workspace_source_kind") is not None
        )

    def _cleanup_provisioned_cloud_binding(self, session: Session) -> None:
        if self._provisioned_cloud_binding_cleanup is None:
            return
        if not self._session_uses_provisioned_cloud_workspace(session):
            return
        workspace = _session_cloud_workspace(session)
        if workspace is None:
            return
        self._provisioned_cloud_binding_cleanup(workspace)

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
        workspace = _session_cloud_workspace(session)
        if workspace is None:
            return None
        return await self._workspace_metadata_store.load_for_session_workspace(
            session_id=session.id,
            workspace_id=workspace.workspace_id,
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

    async def recover_stale_runtime_runs(
        self,
        *,
        recovered_at: datetime | None = None,
    ) -> int:
        return await self._runtime_control_services.run_recovery().recover_stale_runtime_runs(
            recovered_at=recovered_at,
        )

    async def _append_runtime_wire_event(
        self,
        session: Session,
        message: WireMessage,
    ) -> None:
        await RuntimeWireEventRecorder(self._runtime_store).append_wire_event(
            session,
            message,
        )

    def _approval_interactions(self) -> ApprovalInteractionService:
        return ApprovalInteractionService(
            store=self._runtime_store,
            owner_id=self._owner_id,
            fencing_token=self._fencing_token,
        )

    def _approval_decisions(self) -> ApprovalDecisionService:
        return ApprovalDecisionService(
            interactions=self._approval_interactions(),
            persist_session=self._persist_session_async,
        )

    def _approval_requests(self) -> ApprovalRequestService:
        interactions = self._approval_interactions()
        return ApprovalRequestService(
            interactions=interactions,
            decisions=ApprovalDecisionService(
                interactions=interactions,
                persist_session=self._persist_session_async,
            ),
            persist_session=self._persist_session_async,
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
        return cast(
            Session,
            await self._runtime_turn_admission.prepare_session_turn(session_id),
        )

    async def _assert_owner(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
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
        authority = self._owner_authorities.get(session_id)
        if authority is not None:
            if (
                current_owner_id != authority.owner_id
                or current_fencing_token != authority.epoch
            ):
                raise SessionOwnershipConflictError(
                    "stale owner or fencing token rejected"
                )
            return

        if self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        if (
            current_owner_id != self._owner_id
            or current_fencing_token != self._fencing_token
        ):
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

    def _owner_authority_for_session(self, session_id: str) -> OwnerAuthority:
        authority = self._owner_authorities.get(session_id)
        if authority is None:
            raise SessionOwnershipConflictError(
                "durable mutation requires owner authority"
            )
        return authority

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
        payload = cast(dict[str, Any], session.to_store_data())
        if self._local_durable_store is not None:
            authority = self._owner_authorities.get(session.id)
            if authority is None:
                raise SessionOwnershipConflictError(
                    "session metadata mutation requires owner authority"
                )
            await self._local_durable_store.save_session(authority, payload)
            return
        if self._pg_durable_store is not None:
            authority = self._owner_authorities.get(session.id)
            if authority is None:
                raise SessionOwnershipConflictError(
                    "session metadata mutation requires owner authority"
                )
            await self._pg_durable_store.save_session(authority, payload)
            return
        await self._run_store_io(
            self._store.save,
            session.id,
            payload,
        )

    async def _persist_workspace_record_for_session(self, session: Session) -> None:
        store = self._workspace_metadata_store
        if store is None:
            return
        workspace = _session_cloud_workspace(session)
        if workspace is None:
            return
        origin = session.origin or {}
        if (
            origin.get("placement_kind") != "cloud_workspace"
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
        if workspace.runtime_profile is not None:
            source_ref["runtime_profile"] = workspace.runtime_profile
        await store.save(
            WorkspaceRecord(
                workspace_record_id=f"{session.id}:{workspace.workspace_id}",
                workspace_id=workspace.workspace_id,
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
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        acquire_authority = getattr(self._owner_store, "acquire_authority", None)
        if callable(acquire_authority):
            authority = await acquire_authority(
                session_id,
                self._owner_id,
                lease_seconds=self._owner_lease_seconds,
            )
            if not isinstance(authority, OwnerAuthority):
                raise TypeError("acquire_authority must return OwnerAuthority")
            self._owner_authorities[session_id] = authority
            return
        if self._fencing_token is None:
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
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            return False
        authority = self._owner_authorities.get(session_id)
        if authority is not None:
            return (
                owner.owner_id == authority.owner_id
                and owner.fencing_token == authority.epoch
            )
        if self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        return (
            owner.owner_id == self._owner_id
            and owner.fencing_token == self._fencing_token
        )

    async def _holds_active_owner_lease(self, session_id: str) -> bool:
        if self._owner_store is None:
            return False
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            return False
        authority = self._owner_authorities.get(session_id)
        if authority is not None:
            return (
                owner.owner_id == authority.owner_id
                and owner.fencing_token == authority.epoch
                and owner.lease_expires_at > datetime.now(UTC)
            )
        if self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        return (
            owner.owner_id == self._owner_id
            and owner.fencing_token == self._fencing_token
            and owner.lease_expires_at > datetime.now(UTC)
        )

    async def release_owned_sessions(self) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        for session_id in await self.list_sessions_async():
            await self._release_owner_lease_for_session(session_id)

    async def _release_owner_lease_for_session(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        if not await self._holds_owner_lease(session_id):
            return
        authority = self._owner_authorities.get(session_id)
        fencing_token = (
            authority.epoch if authority is not None else self._fencing_token
        )
        if fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        try:
            released = await self._owner_store.release(
                session_id,
                self._owner_id,
                fencing_token,
            )
        except Exception:
            logger.warning(
                "Failed to release owner lease for session %s owned by %s with fencing token %s",
                session_id,
                self._owner_id,
                fencing_token,
                exc_info=True,
            )
            return
        if not released:
            logger.warning(
                "Failed to release owner lease for session %s owned by %s with fencing token %s",
                session_id,
                self._owner_id,
                fencing_token,
            )
            return
        self._owner_authorities.pop(session_id, None)

    async def renew_owner_leases(self) -> list[str]:
        lost_active_sessions: list[str] = []
        if self._owner_store is None:
            return lost_active_sessions
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")
        now = datetime.now(UTC)
        for session_id in await self.list_sessions_async():
            owner = await self._owner_store.get_owner(session_id)
            if owner is None:
                if await self._cancel_active_turn_after_owner_loss(session_id):
                    lost_active_sessions.append(session_id)
                continue
            authority = self._owner_authorities.get(session_id)
            if authority is not None:
                owns_session = (
                    owner.owner_id == authority.owner_id
                    and owner.fencing_token == authority.epoch
                    and owner.lease_expires_at > now
                )
                log_token = authority.epoch
            else:
                if self._fencing_token is None:
                    raise SessionOwnershipConflictError(
                        "stale owner or fencing token rejected"
                    )
                owns_session = (
                    owner.owner_id == self._owner_id
                    and owner.fencing_token == self._fencing_token
                    and owner.lease_expires_at > now
                )
                log_token = self._fencing_token
            if not owns_session:
                if await self._cancel_active_turn_after_owner_loss(session_id):
                    lost_active_sessions.append(session_id)
                continue
            try:
                renew_authority = getattr(self._owner_store, "renew_authority", None)
                if authority is not None and callable(renew_authority):
                    renewed_authority = await renew_authority(
                        authority,
                        lease_seconds=self._owner_lease_seconds,
                    )
                    if not isinstance(renewed_authority, OwnerAuthority):
                        raise TypeError("renew_authority must return OwnerAuthority")
                    self._owner_authorities[session_id] = renewed_authority
                    renewed = True
                else:
                    if self._fencing_token is None:
                        raise SessionOwnershipConflictError(
                            "stale owner or fencing token rejected"
                        )
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
                    log_token,
                    exc_info=True,
                )
                continue
            if not renewed:
                logger.warning(
                    "Failed to renew owner lease for session %s owned by %s with fencing token %s",
                    session_id,
                    self._owner_id,
                    log_token,
                )
                if await self._cancel_active_turn_after_owner_loss(session_id):
                    lost_active_sessions.append(session_id)
        return lost_active_sessions

    async def _cancel_active_turn_after_owner_loss(self, session_id: str) -> bool:
        async with self._lock:
            session = self._session_cache.get(session_id)
            if session is None:
                return False
            if session.task is None or session.task.done():
                return False
            logger.warning(
                "Cancelling active turn for session %s after owner lease loss",
                session_id,
            )
            await self._runtime_cancel_orchestration.cancel(
                session,
                task=session.task,
            )
            return True

    async def backfill_owner_leases(self) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        now = datetime.now(UTC)
        for session_id in await self.list_sessions_async():
            try:
                owner = await self._owner_store.get_owner(session_id)
                if owner is not None and owner.lease_expires_at > now:
                    continue
                acquire_authority = getattr(
                    self._owner_store, "acquire_authority", None
                )
                if callable(acquire_authority):
                    authority = await acquire_authority(
                        session_id,
                        self._owner_id,
                        lease_seconds=self._owner_lease_seconds,
                    )
                    if not isinstance(authority, OwnerAuthority):
                        raise TypeError("acquire_authority must return OwnerAuthority")
                    self._owner_authorities[session_id] = authority
                    acquired = True
                    log_token = authority.epoch
                else:
                    if self._fencing_token is None:
                        raise SessionOwnershipConflictError(
                            "stale owner or fencing token rejected"
                        )
                    acquired = await self._owner_store.acquire(
                        session_id,
                        self._owner_id,
                        lease_seconds=self._owner_lease_seconds,
                        fencing_token=self._fencing_token,
                    )
                    log_token = self._fencing_token
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
                    log_token,
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
        return session.runtime_handle.has_event_queue(queue)

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
        session.runtime_handle.add_event_queue(queue)

    async def register_owned_event_queue_async(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            session.runtime_handle.add_event_queue(queue)
            try:
                await self._assert_owner(session_id)
            except (Exception, asyncio.CancelledError):
                session.runtime_handle.remove_event_queue(queue)
                raise

    async def remove_event_queue_async(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = await self.get_session_async(session_id)
        session.runtime_handle.remove_event_queue(queue)

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
        await self._runtime_closer.close(session)
        await self._finalize_provisioned_cloud_workspace_on_close(session)
        self._session_cache.pop(session_id, None)
        if self._local_durable_store is not None:
            authority = self._owner_authority_for_session(session_id)
            await self._local_durable_store.delete_session(authority)
        elif self._pg_durable_store is not None:
            authority = self._owner_authority_for_session(session_id)
            await self._pg_durable_store.delete_session(authority)
        else:
            await self._run_store_io(self._store.delete, session_id)
        await self._release_owner_lease_for_session(session_id)
        self._approval_stores.pop(session_id, None)
        self._session_turn_locks.pop(session_id, None)

    async def _rollback_partially_created_session(self, session_id: str) -> None:
        self._session_cache.pop(session_id, None)
        try:
            if self._local_durable_store is not None:
                authority = self._owner_authority_for_session(session_id)
                await self._local_durable_store.delete_session(authority)
            elif self._pg_durable_store is not None:
                authority = self._owner_authority_for_session(session_id)
                await self._pg_durable_store.delete_session(authority)
            else:
                await self._run_store_io(self._store.delete, session_id)
        except asyncio.CancelledError:
            pass
        except BaseException:
            logger.exception(
                "Failed to delete partially created session during rollback: %s",
                session_id,
            )
        try:
            await self._release_owner_lease_for_session(session_id)
        except asyncio.CancelledError:
            pass
        except BaseException:
            logger.exception(
                "Failed to release partially created owner lease during rollback: %s",
                session_id,
            )
        self._approval_stores.pop(session_id, None)

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

    def _make_restore_consumer(self, wire: LocalWire) -> LocalWireConsumer:
        async def _reject_approval(req: ApprovalRequest) -> ApprovalResponse:
            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=False,
                feedback="Checkpoint restore does not support approval prompts",
            )

        return LocalWireConsumer(wire, _reject_approval)

    def _make_session_consumer(self, session: Session) -> LocalWireConsumer:
        approval_requests = self._approval_requests()

        async def _request_approval(req: ApprovalRequest) -> ApprovalResponse:
            response = await approval_requests.resolve_session_approval(session, req)
            if response is not None:
                return response
            response = await approval_requests.begin_request(session, req)
            if response is not None:
                return response
            await self._send_session_wire_message(session, req)
            try:
                response = await session.approval_coordinator.wait_for_response(
                    req.request_id,
                    float(req.timeout_seconds),
                )
                if response is None:
                    return await approval_requests.resolve_timeout(session, req)

                await approval_requests.resolve_wait_response(
                    session,
                    req.request_id,
                    response,
                    expose_response=True,
                )
                return response
            finally:
                await approval_requests.cleanup_after_wait(
                    session,
                    signal_event=False,
                )

        return LocalWireConsumer(
            session.wire,
            _request_approval,
            emit_handler=lambda message: self._send_session_wire_message(
                session,
                message,
            ),
        )

    async def _restore_checkpoint(self, session: Session, checkpoint_id: str) -> None:
        await self._runtime_checkpoint_restore_service.restore(session, checkpoint_id)

    async def _restore_checkpoint_durable_state(
        self,
        session: Any,
        snapshot: Any,
    ) -> None:
        typed_session = cast(Session, session)
        if self._local_durable_store is None and self._pg_durable_store is None:
            if typed_session.tape_id is None:
                raise ValueError("session has no stable tape id")
            await self._tape_store.truncate(
                typed_session.tape_id,
                snapshot.meta.entry_count,
            )
            await self._persist_session_async(typed_session)
            checkpoints = await self._checkpoint_service.list(typed_session.tape_id)
            for checkpoint_meta in checkpoints:
                if checkpoint_meta.entry_count > snapshot.meta.entry_count:
                    await self._checkpoint_service.delete(checkpoint_meta.checkpoint_id)
            return
        authority = self._owner_authority_for_session(typed_session.id)
        payload = cast(dict[str, Any], typed_session.to_store_data())
        if self._local_durable_store is not None:
            await self._local_durable_store.restore_checkpoint_state(
                authority,
                snapshot,
                payload,
            )
            return
        if self._pg_durable_store is None:
            raise RuntimeError("durable checkpoint restore store is not configured")
        await self._pg_durable_store.restore_checkpoint_state(
            authority,
            snapshot,
            payload,
        )

    def _persist_session(self, session: Session) -> None:
        if self._local_durable_store is not None or self._pg_durable_store is not None:
            raise RuntimeError(
                "synchronous session persistence is unavailable for fenced durable storage"
            )
        self._session_cache[session.id] = session
        self._store.save(session.id, cast(dict[str, Any], session.to_store_data()))

    def _resolve_environment(self, session: Session) -> Environment:
        return self._runtime_environment_resolver_service.resolve_environment_for_run_target(
            session.default_run_target
        )

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
        default_run_target: RunTarget | None = None,
        mcp_servers: dict[str, dict[str, Any]] | None = None,
        additional_directories: list[str] | None = None,
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
            default_run_target: Explicit placement target. If omitted, a local
                daemon target is derived from repo_path or the current directory.
            mcp_servers: Per-session stdio MCP servers supplied by protocol clients.
            additional_directories: Extra absolute workspace roots supplied by ACP.

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
        target = default_run_target or _local_default_run_target(resolved_repo_path)
        resolved_mcp_servers = _session_mcp_servers_from_store(mcp_servers or {})
        resolved_additional_directories = _session_additional_directories_from_store(
            additional_directories or []
        )

        session = Session(
            id=session_id,
            approval_store=approval_store,
            created_at=now,
            last_activity=now,
            repo_path=resolved_repo_path,
            origin=None if origin is None else dict(origin),
            default_run_target=target,
            approval_policy=approval_policy,
            provider=provider,
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
            max_steps=max_steps,
            mcp_servers=resolved_mcp_servers,
            additional_directories=resolved_additional_directories,
            task=None,
        )

        async with self._lock:
            try:
                await self._acquire_owner_for_session(session_id)
                await self._persist_session_async(session)
                await self._persist_workspace_record_for_session(session)
            except BaseException:
                await self._rollback_partially_created_session(session_id)
                raise

        logger.info(f"Created session: {session_id}")
        return session_id

    async def update_session_mcp_servers(
        self,
        session_id: str,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> None:
        session = await self.get_session_async(session_id)
        await self._assert_owner(session_id)
        resolved_mcp_servers = _session_mcp_servers_from_store(mcp_servers)
        if session.mcp_servers == resolved_mcp_servers:
            return
        await self._runtime_closer.close(session)
        session.mcp_servers = resolved_mcp_servers
        async with self._lock:
            await self._persist_session_async(session)

    async def update_session_additional_directories(
        self,
        session_id: str,
        additional_directories: list[str],
    ) -> None:
        session = await self.get_session_async(session_id)
        await self._assert_owner(session_id)
        resolved_additional_directories = _session_additional_directories_from_store(
            additional_directories
        )
        if session.additional_directories == resolved_additional_directories:
            return
        await self._runtime_closer.close(session)
        session.additional_directories = resolved_additional_directories
        async with self._lock:
            await self._persist_session_async(session)

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

    async def acquire_session_owner(self, session_id: str) -> None:
        await self._acquire_owner_for_session(session_id)

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
        if self._local_durable_store is not None or self._pg_durable_store is not None:
            raise RuntimeError(
                "synchronous session registration is unavailable for fenced durable storage"
            )
        self._runtime_closer.close_sync_safe(session)
        self._approval_stores[session.id] = session.approval_store
        self._persist_session(session)

    def remove_session(self, session_id: str) -> None:
        if self._local_durable_store is not None or self._pg_durable_store is not None:
            raise RuntimeError(
                "synchronous session removal is unavailable for fenced durable storage"
            )
        if not self.has_session(session_id):
            raise KeyError(f"Session not found: {session_id}")
        session = self.get_session(session_id)
        self._runtime_closer.close_sync_safe(session)
        self._cleanup_provisioned_cloud_binding(session)
        self._session_cache.pop(session_id, None)
        self._store.delete(session_id)
        self._approval_stores.pop(session_id, None)
        self._session_turn_locks.pop(session_id, None)

    def clear_sessions(self) -> None:
        if self._local_durable_store is not None or self._pg_durable_store is not None:
            raise RuntimeError(
                "synchronous session clearing is unavailable for fenced durable storage"
            )
        cleared_session_ids = set(self._session_cache)
        for session in list(self._session_cache.values()):
            self._runtime_closer.close_sync_safe(session)
            self._cleanup_provisioned_cloud_binding(session)
        for session_id in list(self._store.list_sessions()):
            if session_id not in cleared_session_ids:
                session = self.get_session(session_id)
                self._runtime_closer.close_sync_safe(session)
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
        session.runtime_handle.add_event_queue(queue)

    def remove_event_queue(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = self.get_session(session_id)
        session.runtime_handle.remove_event_queue(queue)

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

            await self._runtime_control_services.task_stopper().stop(
                session_id=session_id,
                task=session.task,
            )

            await self._remove_session_async_no_lock(session_id)

        logger.info(f"Closed session: {session_id}")

    async def shutdown_session_runtime(
        self,
        session_id: str,
        *,
        interrupt_active_turn: bool = False,
    ) -> None:
        """Release runtime resources without deleting persisted session metadata."""
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            interrupted_run_id = (
                session.current_turn_id
                if interrupt_active_turn
                and session.current_turn_id is not None
                and session.turn_in_progress
                and session.task is not None
                and not session.task.done()
                else None
            )

            await self._runtime_control_services.task_stopper().stop(
                session_id=session_id,
                task=session.task,
            )
            if interrupted_run_id is not None:
                await self._mark_graceful_shutdown_interrupted_run(interrupted_run_id)

            await self._runtime_closer.close(session)
            session.task = None
            session.turn_in_progress = False
            await self._persist_session_async(session)

    async def _mark_graceful_shutdown_interrupted_run(self, run_id: str) -> None:
        store = self._runtime_store
        if store is None:
            return
        run = await store.load_agent_run(run_id)
        if run is None:
            return
        if run.status not in GRACEFUL_SHUTDOWN_INTERRUPTABLE_RUN_STATUSES:
            return

        interrupted_at = datetime.now(UTC)
        metadata = dict(run.metadata)
        metadata["reclaimable"] = True
        metadata["recovered_at"] = interrupted_at.isoformat()
        metadata["recovery_reason"] = GRACEFUL_SHUTDOWN_RECOVERY_REASON
        if self._owner_id is not None:
            metadata["recovered_by_owner_id"] = self._owner_id
        await store.update_agent_run(
            run_id,
            status="interrupted",
            ended_at=interrupted_at,
            metadata=cast(JSONObject, metadata),
            result=run.result,
            error=GRACEFUL_SHUTDOWN_INTERRUPTED_RUN_ERROR,
        )

    async def cancel_session_turn(self, session_id: str) -> CancelTurnResult:
        """Request cancellation for the active turn without closing the session."""
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            result = await self._runtime_cancel_orchestration.cancel(
                session,
                task=session.task,
            )
            return CancelTurnResult(
                session_id=session_id,
                turn_id=result.turn_id,
                status=cast(CancelTurnStatus, result.status),
            )

    def _schedule_cancel_observation(
        self,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        _ = asyncio.create_task(
            self._observe_cancelled_turn(session_id=session_id, task=task)
        )

    async def _observe_cancelled_turn(
        self,
        *,
        session_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        await self._runtime_cancel_observation_finalizer.finalize(
            session_id=session_id,
            task=task,
        )

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
        async def run_admitted_turn(session: object) -> None:
            admitted_session = cast(Session, session)
            run_id = run_id_override or uuid.uuid4().hex
            await self._runtime_turn_service.run(
                admitted_session,
                prompt=prompt,
                run_id=run_id,
                resume_context=resume_context,
                current_task=asyncio.current_task(),
            )

        await self._runtime_turn_admission.run_exclusive(
            session_id,
            run_admitted_turn,
        )

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
        response = await self._approval_decisions().submit(
            session,
            request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )
        if response is not None:
            await RuntimeWireEventRecorder(
                self._runtime_store,
                new_event_id=lambda run_id: (
                    f"{run_id}:wire:approval-response:{request_id}"
                ),
            ).append_wire_event(session, response)
        return response

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
        approval_requests = self._approval_requests()
        if not session.turn_in_progress:
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=False,
                feedback="Approval timeout or error",
            )

        response = await approval_requests.resolve_session_approval(
            session,
            approval_req,
        )
        if response is not None:
            return response

        response = await approval_requests.begin_request(
            session,
            approval_req,
        )
        if response is not None:
            return response

        try:
            response = await session.approval_coordinator.wait_for_response(
                approval_req.request_id,
                float(timeout_seconds),
            )
            if response is not None:
                await approval_requests.resolve_wait_response(
                    session,
                    approval_req.request_id,
                    response,
                    expose_response=False,
                )
                return response
        finally:
            await approval_requests.cleanup_after_wait(
                session,
                signal_event=True,
            )

        return await approval_requests.resolve_timeout(
            session,
            approval_req,
        )

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
        """Shut down runtimes for sessions that have been idle for too long.

        Args:
            max_idle_minutes: Maximum idle time in minutes

        Returns:
            List of session IDs whose runtime was shut down.
        """
        now = datetime.now(UTC)
        shut_down: list[str] = []
        session_ids = await self.list_sessions_async()

        for session_id in session_ids:
            try:
                session = await self.get_session_async(session_id)
                last_activity = session.last_activity
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=UTC)
                idle_time = now - last_activity
                if idle_time.total_seconds() > max_idle_minutes * 60:
                    has_runtime_resources = (
                        session.task is not None
                        or session.runtime_pipeline is not None
                        or session.runtime_ctx is not None
                        or session.runtime_adapter is not None
                    )
                    if has_runtime_resources:
                        await self.shutdown_session_runtime(session_id)
                        shut_down.append(session_id)
            except KeyError:
                # Session was explicitly deleted between list and load.
                pass

        if shut_down:
            logger.info(
                "Shut down %d idle session runtimes: %s",
                len(shut_down),
                shut_down,
            )

        return shut_down

    def _is_local_daemon_run_target(self, target: RunTarget | None) -> bool:
        if target is None:
            return False
        return isinstance(target.executor, LocalDaemonExecutorRef)

    async def _build_session_runtime(
        self,
        session: Session,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None | UnsetType = UNSET,
        max_steps: int | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> tuple[Any, Any, PipelineAdapter]:
        runtime = await self._local_daemon_runtime_preparation.build_runtime(
            session,
            model_name=model_name,
            provider_name=provider_name,
            base_url=base_url,
            max_steps=max_steps,
            approval_policy=approval_policy,
        )
        return (
            runtime.pipeline,
            runtime.ctx,
            cast(PipelineAdapter, runtime.adapter),
        )

    async def ensure_session_runtime(self, session_id: str) -> Any:
        return await self._runtime_ensure_orchestration.ensure_session_runtime(
            session_id
        )

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
        base_url: str | None | UnsetType = UNSET,
    ) -> Session:
        async def replace_admitted_runtime(session: object) -> Session:
            admitted_session = cast(Session, session)
            resolved_model = (
                model_name if model_name is not None else admitted_session.model_name
            )
            if not resolved_model:
                raise RuntimeError(
                    "session is missing model_name, cannot replace runtime"
                )
            return await self._runtime_replacement_service.replace_runtime_config(
                admitted_session,
                model_name=resolved_model,
                provider_name=provider_name,
                base_url=base_url,
                build_runtime=self._build_session_runtime,
                persist_session=self._persist_session_async,
            )

        return cast(
            Session,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                replace_admitted_runtime,
            ),
        )

    async def capture_checkpoint(
        self,
        session_id: str,
        *,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        async def capture_admitted_checkpoint(session: object) -> CheckpointMeta:
            return await self._runtime_checkpoint_capture_service.capture(
                cast(Session, session),
                label=label,
                extra=extra,
            )

        return cast(
            CheckpointMeta,
            await self._runtime_maintenance_admission.run_exclusive(
                session_id,
                capture_admitted_checkpoint,
            ),
        )

    async def export_workspace_archive(
        self,
        session_id: str,
        export_archive: Callable[[CloudWorkspaceRef], T],
    ) -> T:
        return await self._runtime_workspace_export_service.export_archive(
            session_id,
            export_archive,
        )

    async def list_checkpoints(self, session_id: str) -> list[CheckpointMeta]:
        session = await self.get_session_async(session_id)
        return await self._runtime_checkpoint_query_service.list_checkpoints(session)

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        await self._runtime_checkpoint_restore_orchestration.restore_checkpoint(
            session_id,
            checkpoint_id,
        )
