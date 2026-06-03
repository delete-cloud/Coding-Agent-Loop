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
from agentkit.storage.protocols import CheckpointStore, TapeStore
from agentkit.storage.protocols import TapeDebugStore, TapeInfo, TapeSearchResult
from agentkit.tape.tape import Tape
from agentkit.tools import FatalToolExecutionError
from coding_agent.adapter import PipelineAdapter
from coding_agent.agent_observability import (
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
from coding_agent.environment.sandboxed import sandbox_environment
from coding_agent.plugins.storage import JSONLTapeStore
from coding_agent.providers.base import ToolSchema
from coding_agent.runtime_store import (
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
from coding_agent.executors import (
    LocalDaemonExecutor,
)
from coding_agent.events import DisplayEvent
from coding_agent.runs import (
    CHECKPOINT_SESSION_CONFIG_KEY,
    CloudWorkspaceRef,
    CheckpointRestoredRuntime,
    CheckpointRestoreService,
    CheckpointSessionConfig,
    DefaultRunCoordinator,
    EventBroadcastResult,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunCoordinator,
    RuntimeAttachedExecutorService,
    RuntimeAgentFactoryService,
    RuntimeBindingSnapshot,
    RuntimeCancelService,
    RuntimeCloser,
    RuntimeContextBindingService,
    RuntimeEnsureService,
    RuntimeRunMetadataService,
    RuntimeObservationService,
    RuntimePreparationRequestService,
    RuntimeQueryService,
    RuntimeReplacementService,
    RuntimeResumeContext as SessionResumeContext,
    RuntimeResumeService,
    RuntimeRunPersistenceService,
    RuntimeRunRecoveryService,
    RuntimeTaskStopper,
    RuntimeWireEventRecorder,
    RunTarget,
    SessionRuntimeHandle,
    run_target_from_dict,
    run_target_from_execution_binding,
    serialize_checkpoint_session_config,
)
from coding_agent.runs.checkpoint_runtime import CheckpointRuntimeBuilder
from coding_agent.runs.runtime_preparation import LocalDaemonRuntimePreparationService
from coding_agent.runs.turn_execution import RuntimeTurnService
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

_CHECKPOINT_SESSION_CONFIG_KEY = CHECKPOINT_SESSION_CONFIG_KEY
_DEFAULT_EXECUTION_BINDING = object()
T = TypeVar("T")


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
    execution_binding: ExecutionBinding
    default_run_target: RunTarget
    approval_policy: ApprovalPolicy
    provider_name: str | None
    model_name: str | None
    base_url: str | None
    max_steps: int
    tape_id: str | None
    last_failure_details: str | None
    default_run_target_explicit: bool = True

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
        default_run_target_explicit = default_run_target_raw is not None
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
            default_run_target_explicit=default_run_target_explicit,
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
            default_run_target=(
                self.default_run_target if self.default_run_target_explicit else None
            ),
            approval_policy=self.approval_policy,
            provider_name=self.provider_name,
            model_name=self.model_name,
            base_url=self.base_url,
            max_steps=self.max_steps,
            tape_id=self.tape_id,
            last_failure_details=self.last_failure_details,
        )


def _default_run_target_is_derived_from_binding(
    *,
    current_target: object,
    target_explicit: object,
) -> bool:
    if current_target is None:
        return True
    return not bool(target_explicit)


@dataclass
class Session:
    """A managed agent session.

    Note: ``default_run_target`` is the canonical run placement contract.
    ``execution_binding`` remains backward-compatible metadata and seeds the
    default run target when no explicit target has been assigned.
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
            instance_dict = object.__getattribute__(self, "__dict__")
            current_target = instance_dict.get("default_run_target")
            target_explicit = instance_dict.get("_default_run_target_explicit", False)
            object.__setattr__(self, name, value)
            should_sync_target = _default_run_target_is_derived_from_binding(
                current_target=current_target,
                target_explicit=target_explicit,
            )
            if (
                value is not cast(ExecutionBinding, _DEFAULT_EXECUTION_BINDING)
                and should_sync_target
            ):
                object.__setattr__(
                    self,
                    "default_run_target",
                    run_target_from_execution_binding(cast(ExecutionBinding, value)),
                )
                object.__setattr__(self, "_default_run_target_explicit", False)
            return
        if name == "default_run_target":
            object.__setattr__(self, name, value)
            object.__setattr__(self, "_default_run_target_explicit", value is not None)
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
            object.__setattr__(
                self,
                "default_run_target",
                run_target_from_execution_binding(self.execution_binding),
            )
            object.__setattr__(self, "_default_run_target_explicit", False)
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
            default_run_target_explicit=bool(
                getattr(self, "_default_run_target_explicit", True)
            ),
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
        runtime_store: RuntimeStore | None = None,
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
        self._runtime_observation_service = RuntimeObservationService(
            self._agent_observation_store
        )
        self._runtime_metadata_service = RuntimeRunMetadataService()
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
        self._runtime_closer = RuntimeCloser()
        self._runtime_agent_factory_service = RuntimeAgentFactoryService(
            create_agent=self._create_agent,
        )
        self._runtime_preparation_request_service = RuntimePreparationRequestService()
        self._runtime_replacement_service = RuntimeReplacementService(
            close_runtime_adapter=self._runtime_closer.close_adapter,
        )
        self._runtime_ensure_service = RuntimeEnsureService()
        self._runtime_context_binding_service = RuntimeContextBindingService(
            publish_subagent_message=self.publish_subagent_message,
        )
        self._local_daemon_runtime_preparation = LocalDaemonRuntimePreparationService(
            binding_resolver=self._binding_resolver,
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
            adapter_factory=lambda pipeline, ctx, consumer: PipelineAdapter(
                pipeline=pipeline,
                ctx=ctx,
                consumer=consumer,
            ),
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
        runtime_store: RuntimeStore | None,
    ) -> None:
        self._runtime_store = runtime_store
        self._runtime_turn_service = self._build_runtime_turn_service()

    def configure_run_coordinator(self, run_coordinator: RunCoordinator) -> None:
        self._run_coordinator = run_coordinator
        self._runtime_turn_service = self._build_runtime_turn_service()

    def _build_runtime_turn_service(self) -> RuntimeTurnService:
        return RuntimeTurnService(
            run_coordinator=self._run_coordinator,
            runtime_run_persistence=self._runtime_run_persistence(),
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
            fatal_error_types=(FatalToolExecutionError,),
            cancelled_error_types=(asyncio.CancelledError,),
        )

    def _require_runtime_store(self) -> RuntimeStore:
        if self._runtime_store is None:
            raise RuntimeError("runtime store is not configured")
        return self._runtime_store

    async def load_runtime_run(self, run_id: str) -> AgentRunRecord:
        return await self._runtime_queries().load_runtime_run(run_id)

    async def list_runtime_runs(self, session_id: str) -> list[AgentRunRecord]:
        return await self._runtime_queries().list_runtime_runs(session_id)

    async def session_resume_metadata(self, session_id: str) -> dict[str, Any]:
        session = await self.get_session_async(session_id)
        return await self._runtime_queries().session_resume_metadata(
            session,
            list_checkpoints=self.list_checkpoints,
        )

    async def list_runtime_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return await self._runtime_queries().list_runtime_interactions(run_id)

    async def load_runtime_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord:
        return await self._runtime_queries().load_runtime_interaction(interaction_id)

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
        return await self._runtime_queries().load_runtime_message_snapshot(run_id)

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        return await self._runtime_queries().replay_runtime_events(
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
        return await self._runtime_queries().replay_display_events(
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
            record = await self._runtime_attached_executor_service().request_run(
                session,
                prompt=prompt,
                run_id=resolved_run_id,
                resume_context=resume_context,
            )
            now = record.started_at
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
        previous_run = await self._runtime_queries().latest_runtime_run(session_id)
        if previous_run is None:
            raise RuntimeError("session has no previous run to resume")
        if previous_run.status in _ACTIVE_RESUME_BLOCKING_RUN_STATUSES:
            raise RuntimeError("latest run is still active")
        if session.tape_id is None and previous_run.tape_id is not None:
            session.tape_id = previous_run.tape_id
            await self._persist_session_async(session)
        resume_context = await self._runtime_resume_service().build_context(
            session=session,
            previous_run=previous_run,
            resume_from_event_id=await self._runtime_queries().latest_runtime_event_id(
                previous_run
            ),
            resume_reason=resume_reason,
            list_checkpoints=self.list_checkpoints,
            load_tape_entries=self._tape_store.load,
            load_message_snapshot=self._require_runtime_store().load_message_snapshot,
        )
        resume_context = await self._append_resume_boundary_anchor(
            session,
            resume_context,
        )
        resume_prompt = self._runtime_resume_service().resume_prompt(
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
        anchor = self._runtime_resume_service().resume_boundary_anchor(resume_context)
        await self._tape_store.save(session.tape_id, [anchor.to_dict()])
        runtime_ctx = session.runtime_ctx
        tape = getattr(runtime_ctx, "tape", None)
        if isinstance(tape, Tape) and tape.tape_id == session.tape_id:
            tape.append(anchor)
        return self._runtime_resume_service().bind_boundary_anchor(
            resume_context,
            anchor_id=anchor.id,
        )

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
        claim = await self._runtime_attached_executor_service().claim_run(
            executor_id=executor_id,
            session_id=session_id,
            executor_kind=executor_kind,
            lease_seconds=lease_seconds,
            worker_instance_id=worker_instance_id,
            process_id=process_id,
            capabilities=capabilities,
            workspace_sync=workspace_sync,
        )
        if claim is None:
            return None
        session = await self.get_session_async(claim.run.session_id)
        return ExternalWorkerClaim(
            run=claim.run,
            claim_token=claim.claim_token,
            prompt=claim.prompt,
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
        return await self._runtime_attached_executor_service().heartbeat_run(
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
        return await self._runtime_attached_executor_service().append_event(
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
        attached_executor_service = self._runtime_attached_executor_service()
        run = await attached_executor_service.load_and_authorize_run(
            run_id=run_id,
            executor_id=executor_id,
            claim_token=claim_token,
        )
        attached_executor_service.validate_final_status(status)
        if tape_id is not None and tape_entries is not None:
            await self._tape_store.save(tape_id, tape_entries)
        updated = await attached_executor_service.finalize_authorized_run(
            run,
            status=status,
            result=result,
            error=error,
            tape_id=tape_id,
        )
        async with self._lock:
            session = await self.get_session_async(updated.session_id)
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
            path_obj = self._storage_config.get("runtime_path")
            path = (
                Path(path_obj)
                if isinstance(path_obj, str) and path_obj.strip()
                else Path(os.environ.get("AGENT_DATA_DIR", "./data"))
                / "runtime.sqlite3"
            )
            return SQLiteRuntimeStore(path)
        raise ValueError(f"unsupported storage.runtime_backend: {backend}")

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

    def _runtime_run_persistence(self) -> RuntimeRunPersistenceService:
        return RuntimeRunPersistenceService(
            run_store=self._runtime_store,
            checkpoint_store=self._runtime_store,
            metadata_for_session=self._runtime_metadata_service.metadata_for_session,
        )

    def _runtime_run_recovery(self) -> RuntimeRunRecoveryService:
        async def session_is_recoverable(session_id: str) -> bool:
            if self._owner_store is None:
                return True
            return await self._holds_active_owner_lease(session_id)

        return RuntimeRunRecoveryService(
            store=self._runtime_store,
            list_session_ids=self.list_sessions_async,
            session_is_recoverable=session_is_recoverable,
            owner_id=self._owner_id,
        )

    def _runtime_queries(self) -> RuntimeQueryService:
        return RuntimeQueryService(
            self._runtime_store,
            active_resume_blocking_statuses=frozenset(
                _ACTIVE_RESUME_BLOCKING_RUN_STATUSES
            ),
        )

    def _runtime_resume_service(self) -> RuntimeResumeService:
        return RuntimeResumeService()

    def _runtime_attached_executor_service(self) -> RuntimeAttachedExecutorService:
        return RuntimeAttachedExecutorService(
            store=self._runtime_store,
            metadata_for_session=self._runtime_metadata_service.metadata_for_session,
        )

    def _runtime_cancel_service(self) -> RuntimeCancelService:
        return RuntimeCancelService(store=self._runtime_store)

    def _runtime_task_stopper(self) -> RuntimeTaskStopper:
        return RuntimeTaskStopper()

    async def recover_stale_runtime_runs(
        self,
        *,
        recovered_at: datetime | None = None,
    ) -> int:
        return await self._runtime_run_recovery().recover_stale_runtime_runs(
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

    def _checkpoint_restore_service(self) -> CheckpointRestoreService:
        runtime_builder = CheckpointRuntimeBuilder(
            local_daemon_executor=self._local_daemon_executor,
            resolve_environment_for_run_target=self._resolve_environment_for_run_target,
            workspace_root_for_environment=self._environment_workspace_root,
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
        )

        async def prepare_runtime(
            *,
            session: Session,
            restored_tape: Tape,
            restored_config: CheckpointSessionConfig,
            plugin_states: Mapping[str, Any],
        ) -> CheckpointRestoredRuntime:
            return await runtime_builder.prepare_runtime(
                session=session,
                restored_tape=restored_tape,
                restored_config=restored_config,
                plugin_states=plugin_states,
            )

        return CheckpointRestoreService(
            checkpoint_service=self._checkpoint_service,
            tape_store=self._tape_store,
            prepare_runtime=prepare_runtime,
            close_runtime=self._runtime_closer.close,
            persist_session=self._persist_session_async,
        )

    async def _restore_checkpoint(self, session: Session, checkpoint_id: str) -> None:
        await self._checkpoint_restore_service().restore(session, checkpoint_id)

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
        environment: Environment
        if isinstance(workspace, LocalPathWorkspaceRef):
            environment = LocalEnvironment(Path(workspace.path).expanduser().resolve())
            return sandbox_environment(environment, target.isolation)
        if isinstance(workspace, CloudWorkspaceRef):
            environment = self._binding_resolver.resolve_environment(
                CloudWorkspaceBinding(
                    workspace_url=workspace.workspace_url,
                    workspace_id=workspace.workspace_id,
                    runtime_profile=workspace.runtime_profile,
                    workspace_provider=workspace.workspace_provider,
                    provider_instance_id=workspace.provider_instance_id,
                )
            )
            return sandbox_environment(environment, target.isolation)
        raise ValueError(
            f"runtime builders cannot resolve workspace target: {workspace.kind}"
        )

    def _environment_workspace_root(self, environment: Environment) -> Path | None:
        local_root = environment.workspace_summary().local_root
        if local_root is None:
            return None
        return Path(local_root).expanduser().resolve()

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
        self._runtime_closer.close_sync_safe(session)
        self._approval_stores[session.id] = session.approval_store
        self._persist_session(session)

    def remove_session(self, session_id: str) -> None:
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

            await self._runtime_task_stopper().stop(
                session_id=session_id,
                task=session.task,
            )

            await self._remove_session_async_no_lock(session_id)

        logger.info(f"Closed session: {session_id}")

    async def shutdown_session_runtime(self, session_id: str) -> None:
        """Release runtime resources without deleting persisted session metadata."""
        async with self._lock:
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)

            await self._runtime_task_stopper().stop(
                session_id=session_id,
                task=session.task,
            )

            await self._runtime_closer.close(session)
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
                result = (
                    await self._runtime_cancel_service().cancel_attached_executor_turn(
                        session
                    )
                )
                await self._persist_session_async(session)
                return CancelTurnResult(
                    session_id=session_id,
                    turn_id=result.turn_id,
                    status=cast(CancelTurnStatus, result.status),
                )
            if task is None or task.done():
                result = (
                    self._runtime_cancel_service().cancel_idle_or_finished_local_turn(
                        session
                    )
                )
                await self._persist_session_async(session)
                return CancelTurnResult(
                    session_id=session_id,
                    turn_id=result.turn_id,
                    status=cast(CancelTurnStatus, result.status),
                )

            if session.current_turn_id is None:
                session.current_turn_id = uuid.uuid4().hex
            self._runtime_cancel_service().mark_cancelling(session)
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
        final_status = (
            await self._runtime_cancel_service().observe_cancelled_local_task(task)
        )

        async with self._lock:
            try:
                session = await self.get_session_async(session_id)
            except KeyError:
                return
            if session.task is not task:
                return
            session.task = None
            self._runtime_cancel_service().finish_observed_local_turn(
                session,
                status=final_status,
            )
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
            run_id = run_id_override or uuid.uuid4().hex
            await self._runtime_turn_service.run(
                session,
                prompt=prompt,
                run_id=run_id,
                resume_context=resume_context,
                current_task=asyncio.current_task(),
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
        return await self._approval_decisions().submit(
            session,
            request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )

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
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        return await self._runtime_ensure_service.ensure_runtime(
            session,
            build_runtime=self._build_session_runtime,
            persist_session=self._persist_session_async,
        )

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

            return await self._runtime_replacement_service.replace_runtime_config(
                session,
                model_name=model_name,
                build_runtime=self._build_session_runtime,
                persist_session=self._persist_session_async,
            )

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
                serialize_checkpoint_session_config(session)
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
