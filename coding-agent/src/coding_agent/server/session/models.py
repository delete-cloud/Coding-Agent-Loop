"""Live Session object and small session API types."""

from __future__ import annotations

import logging
import asyncio
import uuid
from collections.abc import (
    AsyncIterator,
    Mapping,
)
from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    ClassVar,
    Literal,
    Protocol,
    TypeVar,
    cast,
)
from agentkit.providers.models import (
    DoneEvent,
    TextEvent,
)
from agentkit.runtime import (
    InMemoryRuntimeMessageBus,
    RuntimeMessageBus,
    RuntimeMessageCursor,
)
from coding_agent.approval import (
    ApprovalCoordinator,
    ApprovalPolicy,
)
from coding_agent.approval.store import ApprovalStore
from coding_agent.providers.base import ToolSchema
from coding_agent.stores.runtime_store import AgentRunRecord
from coding_agent.topics.memory import MemoryReviewStore
from coding_agent.runs import (
    EventBroadcastResult,
    LocalDaemonExecutorRef,
    RuntimeBindingSnapshot,
    CloudWorkspaceRef,
    ExternalWorkerExecutorRef,
    IsolationPolicy,
    LocalAttachedExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
    SessionRuntimeHandle,
)
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import ApprovalRequest
from coding_agent.server.stores.workspace_store import (
    JSONValue,
    WorkspaceRecord,
    WorkspaceRetentionPolicy,
    WorkspaceStatus,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coding_agent.server.session.records import SessionRecord

logger = logging.getLogger("coding_agent.server.session_manager")

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


TurnStatus = Literal["idle", "running", "cancelling", "cancelled", "failed"]

CancelTurnStatus = Literal["idle", "cancelling", "cancelled", "failed"]


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
        from coding_agent.server.session.records import SessionRecord

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
            current_turn_id=self.current_turn_id,
        )

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> Session:
        from coding_agent.server.session.records import SessionRecord

        session = SessionRecord.from_store_data(data).to_session()
        session.turn_in_progress = False
        session.clear_approval_runtime_state()
        return session
