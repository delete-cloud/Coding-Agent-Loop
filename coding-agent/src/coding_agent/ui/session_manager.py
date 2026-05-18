"""SessionManager for managing agent sessions."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from collections.abc import Callable
from functools import partial
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from inspect import isawaitable
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from agentkit.environment import Environment
from agentkit.storage.checkpoint_fs import FSCheckpointStore
from agentkit.storage.pg import PGPool
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
from agentkit.storage.protocols import CheckpointStore, TapeStore
from agentkit.tape.tape import Tape
from agentkit.tools import FatalToolExecutionError
from agentkit.tape.models import Entry
from coding_agent.adapter import PipelineAdapter
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
from coding_agent.runtime_store import AgentRunRecord, JSONObject
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    TurnEnd,
    WireMessage,
)
from coding_agent.ui.session_store import (
    SessionStore,
    create_session_store,
)
from coding_agent.ui.session_owner_store import SessionOwnerStoreProtocol
from coding_agent.ui.session_owner_store import SessionOwnershipConflictError
from coding_agent.ui.session_owner_store import SessionOwnershipConflictReason
from coding_agent.ui.binding_resolver import BindingResolver, DefaultBindingResolver
from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)
from coding_agent.ui.workspace_store import (
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


def _subagent_message_id(session_id: str) -> str:
    return f"subagent_message:{session_id}:{uuid.uuid4().hex}"


def _approval_response_projection(response: ApprovalResponse) -> dict[str, Any]:
    return {
        "request_id": response.request_id,
        "decision": "approve" if response.approved else "deny",
        "feedback": response.feedback,
    }


@dataclass(frozen=True, slots=True)
class _PublishedApprovalDecision:
    sequence: int
    response: ApprovalResponse


TurnStatus = Literal["idle", "running", "cancelling", "cancelled", "failed"]
CancelTurnStatus = Literal["idle", "cancelling", "cancelled", "failed"]


@dataclass(frozen=True)
class CancelTurnResult:
    session_id: str
    turn_id: str | None
    status: CancelTurnStatus


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
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: Any | None = None
    provider_name: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    max_steps: int = 30
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
        self.wire = LocalWire(self.id)
        self.approval_coordinator = ApprovalCoordinator(self.approval_store)

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
            "workspace_id": workspace_id,
        }

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
            "approval_policy": self.approval_policy.value,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
            "tape_id": self.tape_id,
            "last_failure_details": self.last_failure_details,
        }

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> Session:
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
        session = cls(
            id=_required_session_str(data, "id"),
            created_at=datetime.fromisoformat(
                _required_session_str(data, "created_at")
            ),
            last_activity=datetime.fromisoformat(
                _required_session_str(data, "last_activity")
            ),
            approval_store=ApprovalStore(),
            repo_path=None if repo_path_raw is None else Path(repo_path_raw),
            origin=origin,
            execution_binding=execution_binding,
            approval_policy=ApprovalPolicy(approval_policy_raw),
            provider_name=provider_name_raw,
            model_name=model_name_raw,
            base_url=base_url_raw,
            max_steps=_required_session_int(data, "max_steps"),
            tape_id=tape_id_raw,
            last_failure_details=last_failure_details_raw,
        )
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
    ) -> None:
        self._wire = wire
        self._approval_handler = approval_handler

    async def emit(self, msg: WireMessage) -> None:
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
        owner_store: SessionOwnerStoreProtocol | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
        owner_lease_seconds: float = 30.0,
    ):
        self._storage_config = storage_config or {}
        self._pg_pool = pg_pool
        self._owns_pg_pool = False
        self._store = store or self._create_http_session_store()
        self._session_cache: dict[str, Session] = {}
        self._approval_stores: dict[str, ApprovalStore] = {}
        self._lock = asyncio.Lock()
        self._store_io_guard = asyncio.Lock()
        self._session_turn_locks: dict[str, asyncio.Lock] = {}
        self._session_workspace_export_counts: dict[str, int] = {}
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        self._tape_store = tape_store or self._create_tape_store(data_dir)
        resolved_checkpoint_store = checkpoint_store or self._create_checkpoint_store(
            data_dir
        )
        self._checkpoint_service = checkpoint_service or CheckpointService(
            resolved_checkpoint_store
        )
        self._create_agent = create_agent_fn
        self._binding_resolver = binding_resolver or DefaultBindingResolver()
        self._provisioned_cloud_binding_cleanup = provisioned_cloud_binding_cleanup
        self._workspace_metadata_store = workspace_metadata_store
        self._runtime_store = runtime_store
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
        return create_session_store(
            backend=backend,
            dsn=dsn if isinstance(dsn, str) else None,
            pg_pool=None,
        )

    def _create_tape_store(self, data_dir: Path) -> TapeStore:
        backend = str(self._storage_config.get("tape_backend", "jsonl")).strip().lower()
        if backend == "pg":
            _, PGTapeStore, _ = _load_pg_storage_types()
            return cast(TapeStore, PGTapeStore(pool=self._get_pg_pool()))
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
        return FSCheckpointStore(data_dir / "checkpoints")

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

    def _bind_root_run_identity(self, session: Session, ctx: Any, run_id: str) -> None:
        if hasattr(ctx, "session_id"):
            ctx.session_id = session.id
        run_context = getattr(ctx, "run_context", None)
        if run_context is not None:
            if not isinstance(run_context, AgentRunContext):
                raise TypeError("runtime context run_context must be AgentRunContext")
            ctx.run_context = replace(
                run_context,
                session_id=session.id,
                run_id=run_id,
                parent_run_id=None,
            )

    def _run_metadata_for_session(self, session: Session) -> JSONObject:
        return {
            "provider_name": session.provider_name,
            "model_name": session.model_name,
            "approval_policy": session.approval_policy.value,
            "max_steps": session.max_steps,
        }

    def _result_from_turn_outcome(self, outcome: TurnOutcome) -> JSONObject:
        return {
            "stop_reason": outcome.stop_reason.value,
            "steps_taken": outcome.steps_taken,
        }

    def _status_from_turn_outcome(self, outcome: TurnOutcome) -> str:
        if outcome.error is not None or outcome.stop_reason == StopReason.ERROR:
            return "failed"
        if outcome.stop_reason == StopReason.INTERRUPTED:
            return "cancelled"
        return "succeeded"

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
    ) -> bool:
        if self._runtime_store is None:
            return False
        await self._runtime_store.create_agent_run(
            AgentRunRecord(
                run_id=run_id,
                session_id=session.id,
                tape_id=session.tape_id,
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
                metadata=self._run_metadata_for_session(session),
                result={},
                error=None,
            )
        )
        return True

    async def _finish_runtime_agent_run(
        self,
        session: Session,
        *,
        run_id: str,
        status: str,
        result: JSONObject,
        error: str | None,
    ) -> None:
        if self._runtime_store is None:
            return
        await self._runtime_store.update_agent_run(
            run_id,
            status=status,
            ended_at=datetime.now(UTC),
            metadata=self._run_metadata_for_session(session),
            result=result,
            error=error,
        )

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
        async with self._store_io_guard:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, partial(func, *args))

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
                return ApprovalResponse(
                    session_id=req.session_id,
                    request_id=req.request_id,
                    approved=True,
                    scope="session",
                )
            session.approval_coordinator.add_request(req)
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_event.clear()
            session.approval_response = None
            await self._persist_session_async(session)
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
            await session.wire.send(req)
            try:
                response = await session.approval_coordinator.wait_for_response(
                    req.request_id,
                    float(req.timeout_seconds),
                )
                if response is None:
                    return ApprovalResponse(
                        session_id=req.session_id,
                        request_id=req.request_id,
                        approved=False,
                        feedback="Approval timeout or error",
                    )

                session.approval_response = {
                    "decision": "approve" if response.approved else "deny",
                    "feedback": response.feedback,
                }
                session.approval_event.set()
                session.pending_approval = session.approval_coordinator.projection()
                await self._persist_session_async(session)
                return response
            finally:
                session.pending_approval = session.approval_coordinator.projection()
                session.approval_response = None
                await self._persist_session_async(session)

        return _WireConsumer(session.wire, _request_approval)

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
        environment = self._resolve_environment(session)
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

    def _environment_workspace_root(self, environment: Environment) -> Path | None:
        local_root = environment.workspace_summary().local_root
        if local_root is None:
            return None
        return Path(local_root).expanduser().resolve()

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
        now = datetime.now()

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
        event: dict[str, str],
    ) -> None:
        session = self.get_session(session_id)
        before_count = len(session.event_queues)
        session.event_queues = [
            queue for queue in session.event_queues if not queue.full()
        ]
        pruned_count = before_count - len(session.event_queues)
        if pruned_count:
            logger.info(
                "Pruned %d full event queue(s) for session %s",
                pruned_count,
                session_id,
            )
        for queue in session.event_queues:
            try:
                await queue.put(event)
            except Exception:
                logger.debug("Dropping closed event queue", exc_info=True)

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
                session.last_activity = datetime.now()
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
            session.last_activity = datetime.now()
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
            session.last_activity = datetime.now()
            await self._persist_session_async(session)

    async def close(self) -> None:
        await self._close_resource_async(self._store)
        if self._owns_pg_pool:
            await self._close_resource_async(self._pg_pool)

    async def run_agent(
        self,
        session_id: str,
        prompt: str,
    ) -> None:
        turn_lock = self._turn_lock_for(session_id)
        if turn_lock.locked():
            raise RuntimeError("turn already in progress")

        async with turn_lock:
            if self._workspace_export_in_progress(session_id):
                raise RuntimeError("turn already in progress")
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            session.last_activity = datetime.now()
            session.turn_in_progress = True
            session.turn_status = "running"
            run_id = uuid.uuid4().hex
            started_at = datetime.now(UTC)
            session.current_turn_id = run_id
            session.last_failure_details = None
            await self._persist_session_async(session)
            agent_run_created = False

            try:
                approval_mode_map = {
                    ApprovalPolicy.YOLO: "yolo",
                    ApprovalPolicy.INTERACTIVE: "interactive",
                    ApprovalPolicy.AUTO: "auto",
                }

                consumer = self._make_session_consumer(session)
                pipeline = session.runtime_pipeline
                ctx = session.runtime_ctx
                adapter = session.runtime_adapter

                if pipeline is None or ctx is None or adapter is None:
                    environment = self._resolve_environment(session)
                    workspace_root = self._environment_workspace_root(environment)
                    pipeline, ctx = self._create_agent_for_session(
                        workspace_root=workspace_root,
                        environment=environment,
                        model_override=session.model_name,
                        provider_override=session.provider_name,
                        base_url_override=session.base_url,
                        max_steps_override=session.max_steps,
                        approval_mode_override=approval_mode_map[
                            session.approval_policy
                        ],
                        session_id_override=session_id,
                        run_id_override=run_id,
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

                    adapter = PipelineAdapter(
                        pipeline=pipeline, ctx=ctx, consumer=consumer
                    )
                    session.runtime_pipeline = pipeline
                    session.runtime_ctx = ctx
                    session.runtime_adapter = adapter

                self._bind_root_run_identity(session, ctx, run_id)
                agent_run_created = await self._create_runtime_agent_run(
                    session,
                    run_id=run_id,
                    started_at=started_at,
                )
                set_consumer = getattr(adapter, "set_consumer", None)
                if callable(set_consumer):
                    set_consumer(consumer)
                ctx.runtime_message_bus = session.runtime_message_bus
                ctx.config["wire_consumer"] = consumer
                self._bind_subagent_message_publisher(ctx)
                outcome = await adapter.run_turn(prompt)
                if self._runtime_store is not None:
                    turn_outcome = self._require_turn_outcome(outcome)
                    await self._finish_runtime_agent_run(
                        session,
                        run_id=run_id,
                        status=self._status_from_turn_outcome(turn_outcome),
                        result=self._result_from_turn_outcome(turn_outcome),
                        error=turn_outcome.error,
                    )
                session.tape_id = ctx.tape.tape_id
                session.last_failure_details = None
                await self._persist_session_async(session)
            except FatalToolExecutionError as exc:
                if agent_run_created:
                    await self._finish_runtime_agent_run(
                        session,
                        run_id=run_id,
                        status="failed",
                        result={},
                        error=str(exc),
                    )
                session.turn_status = "failed"
                session.last_failure_details = f"Fatal tool execution failed: {exc}"
                await self._close_runtime(session)
                raise
            except asyncio.CancelledError:
                if agent_run_created:
                    await self._finish_runtime_agent_run(
                        session,
                        run_id=run_id,
                        status="cancelled",
                        result={},
                        error="cancelled",
                    )
                raise
            except Exception as exc:
                if agent_run_created:
                    await self._finish_runtime_agent_run(
                        session,
                        run_id=run_id,
                        status="failed",
                        result={},
                        error=str(exc),
                    )
                session.turn_status = "failed"
                session.last_failure_details = f"HTTP session turn failed: {exc}"
                await self._close_runtime(session)
                logger.exception("HTTP session turn failed")
                await session.wire.send(
                    StreamDelta(
                        session_id=session_id,
                        agent_id="",
                        content=f"Error: {exc}",
                    )
                )
                await session.wire.send(
                    TurnEnd(
                        session_id=session_id,
                        agent_id="",
                        turn_id=run_id,
                        completion_status=CompletionStatus.ERROR,
                    )
                )
            finally:
                current_task = asyncio.current_task()
                if session.task is None or session.task is not current_task:
                    session.turn_in_progress = False
                    if session.turn_status == "running":
                        session.turn_status = "idle"
                session.last_activity = datetime.now()
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

        session.last_activity = datetime.now()
        session.pending_approval = session.approval_coordinator.projection()
        session.approval_response = _approval_response_projection(decision.response)
        session.approval_event.set()
        await self._persist_session_async(session)
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
        session.last_activity = datetime.now()

        if success:
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_response = _approval_response_projection(
                published_decision.response
            )
            session.approval_event.set()
            await self._persist_session_async(session)
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
            session.last_activity = datetime.now()
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
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=True,
                scope="session",
            )

        session.approval_coordinator.add_request(approval_req)
        session.pending_approval = session.approval_coordinator.projection()
        session.approval_event.clear()
        session.approval_response = None
        await self._persist_session_async(session)
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
                return response
        finally:
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_response = None
            _ = session.approval_event.set()
            await self._persist_session_async(session)

        return ApprovalResponse(
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Approval timeout or error",
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
        now = datetime.now()
        closed: list[str] = []
        session_ids = await self.list_sessions_async()

        for session_id in session_ids:
            try:
                session = await self.get_session_async(session_id)
                idle_time = now - session.last_activity
                if idle_time.total_seconds() > max_idle_minutes * 60:
                    await self.close_session(session_id)
                    closed.append(session_id)
            except KeyError:
                # Session already closed
                pass

        if closed:
            logger.info(f"Cleaned up {len(closed)} idle sessions: {closed}")

        return closed

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
        environment = self._resolve_environment(session)
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
