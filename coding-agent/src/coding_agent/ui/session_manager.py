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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from inspect import isawaitable
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from agentkit.storage.checkpoint_fs import FSCheckpointStore
from agentkit.checkpoint.models import CheckpointMeta
from agentkit.checkpoint import CheckpointService
from agentkit.storage.protocols import CheckpointStore, TapeStore
from coding_agent.adapter import PipelineAdapter
from coding_agent.approval import ApprovalCoordinator, ApprovalPolicy
from coding_agent.approval.store import ApprovalStore
from coding_agent.core import config as core_config
from coding_agent.plugins.storage import JSONLTapeStore
from coding_agent.providers.base import ChatProvider, ToolSchema
from agentkit.providers.models import DoneEvent, TextEvent
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)
from coding_agent.ui.session_store import (
    AsyncPGSessionPool,
    SessionStore,
    create_session_store,
)
from coding_agent.ui.session_owner_store import SessionOwnerStoreProtocol
from coding_agent.ui.session_owner_store import SessionOwnershipConflictError
from coding_agent.ui.binding_resolver import BindingResolver, DefaultBindingResolver
from coding_agent.ui.execution_binding import ExecutionBinding, LocalExecutionBinding

logger = logging.getLogger(__name__)

_CHECKPOINT_SESSION_CONFIG_KEY = "session_restart_config"
T = TypeVar("T")


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

    Note: ``repo_path`` is legacy/backward-compat metadata only;
    ``execution_binding`` is the authoritative workspace contract.
    """

    id: str
    created_at: datetime
    last_activity: datetime
    wire: LocalWire = field(init=False)
    approval_store: ApprovalStore = field(default_factory=ApprovalStore)
    repo_path: Path | None = None  # legacy/backward-compat metadata only
    execution_binding: ExecutionBinding = field(
        default_factory=lambda: LocalExecutionBinding(
            workspace_root=str(Path.cwd().resolve())
        )
    )
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: Any | None = None
    provider_name: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    max_steps: int = 30
    task: asyncio.Task[Any] | None = None
    turn_in_progress: bool = False
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    tape_id: str | None = None
    runtime_pipeline: Any | None = None
    runtime_ctx: Any | None = None
    runtime_adapter: Any | None = None
    approval_coordinator: ApprovalCoordinator = field(init=False)

    def __post_init__(self) -> None:
        self.wire = LocalWire(self.id)
        self.approval_coordinator = ApprovalCoordinator(self.approval_store)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "turn_in_progress": self.turn_in_progress,
            "pending_approval": self.pending_approval is not None,
        }

    def to_store_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            # repo_path is legacy/backward-compat metadata; execution_binding is
            # the authoritative workspace contract.
            "repo_path": None if self.repo_path is None else str(self.repo_path),
            "execution_binding": self.execution_binding.to_dict(),
            "approval_policy": self.approval_policy.value,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
            "tape_id": self.tape_id,
        }

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> Session:
        repo_path_raw = data.get("repo_path")
        if repo_path_raw is not None and not isinstance(repo_path_raw, str):
            raise TypeError("session metadata has invalid repo_path")
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
        binding_raw = data.get("execution_binding")
        if binding_raw is not None:
            if not isinstance(binding_raw, dict):
                raise TypeError("session metadata has invalid execution_binding")
            execution_binding = ExecutionBinding.from_dict(binding_raw)
        else:
            workspace_root = (
                repo_path_raw if repo_path_raw is not None else str(Path.cwd())
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
            execution_binding=execution_binding,
            approval_policy=ApprovalPolicy(approval_policy_raw),
            provider_name=provider_name_raw,
            model_name=model_name_raw,
            base_url=base_url_raw,
            max_steps=_required_session_int(data, "max_steps"),
            tape_id=tape_id_raw,
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
        owner_store: SessionOwnerStoreProtocol | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
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
        if owner_store is None and (owner_id is not None or fencing_token is not None):
            raise ValueError(
                "owner_store must be provided when owner_id or fencing_token is set"
            )
        if owner_store is not None and (owner_id is None or fencing_token is None):
            raise ValueError(
                "owner_id and fencing_token must be provided when owner_store is set"
            )
        self._owner_store = owner_store
        self._owner_id = owner_id
        self._fencing_token = fencing_token

    def _get_pg_pool(self) -> AsyncPGSessionPool:
        if self._pg_pool is not None:
            return cast(AsyncPGSessionPool, self._pg_pool)

        PGPool, _, _ = _load_pg_storage_types()

        dsn_obj = self._storage_config.get("dsn")
        if not isinstance(dsn_obj, str) or not dsn_obj.strip():
            raise RuntimeError("PG storage requires storage_config['dsn']")
        dsn = dsn_obj.strip()
        self._pg_pool = PGPool(dsn=dsn)
        self._owns_pg_pool = True
        return cast(AsyncPGSessionPool, self._pg_pool)

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

    def _create_agent_for_session(self, **kwargs: Any) -> tuple[Any, Any]:
        factory = self._create_agent
        if factory is None:
            factory = importlib.import_module("coding_agent.__main__").create_agent
        return factory(**kwargs)

    def _turn_lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._session_turn_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_turn_locks[session_id] = lock
        return lock

    async def _assert_owner(self, session_id: str) -> None:
        if self._owner_store is None:
            return
        if self._owner_id is None or self._fencing_token is None:
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        owner = await self._owner_store.get_owner(session_id)
        if owner is None:
            raise SessionOwnershipConflictError("session has no owner")
        if owner.lease_expires_at <= datetime.now(UTC):
            raise SessionOwnershipConflictError("session owner lease expired")

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
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        session.event_queues.append(queue)
        try:
            await self._assert_owner(session_id)
        except SessionOwnershipConflictError:
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

    async def remove_session_async(self, session_id: str) -> None:
        session = await self.get_session_async(session_id)
        await self._close_runtime(session)
        self._session_cache.pop(session_id, None)
        await self._run_store_io(self._store.delete, session_id)
        self._approval_stores.pop(session_id, None)
        self._session_turn_locks.pop(session_id, None)

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

        approval_mode_map = {
            ApprovalPolicy.YOLO: "yolo",
            ApprovalPolicy.INTERACTIVE: "interactive",
            ApprovalPolicy.AUTO: "auto",
        }
        pipeline, ctx = self._create_agent_for_session(
            workspace_root=self._resolve_workspace_root(session),
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

    def _resolve_workspace_root(self, session: Session) -> Path:
        return self._binding_resolver.resolve_workspace_root(session.execution_binding)

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
        approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        provider: Any | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        max_steps: int = 30,
        enable_parallel: bool = True,
        max_parallel: int = 5,
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
        binding = LocalExecutionBinding(
            workspace_root=(
                str(resolved_repo_path)
                if resolved_repo_path is not None
                else str(Path.cwd().resolve())
            )
        )

        session = Session(
            id=session_id,
            approval_store=approval_store,
            created_at=now,
            last_activity=now,
            repo_path=resolved_repo_path,
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
            await self._persist_session_async(session)

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
        self._session_cache.pop(session_id, None)
        self._store.delete(session_id)
        self._approval_stores.pop(session_id, None)
        self._session_turn_locks.pop(session_id, None)

    def clear_sessions(self) -> None:
        for session in list(self._session_cache.values()):
            self._close_runtime_sync_safe(session)
        for session_id in list(self._store.list_sessions()):
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

            await self.remove_session_async(session_id)

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
            await self._assert_owner(session_id)
            session = await self.get_session_async(session_id)
            session.last_activity = datetime.now()
            session.turn_in_progress = True
            await self._persist_session_async(session)

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
                    pipeline, ctx = self._create_agent_for_session(
                        workspace_root=self._resolve_workspace_root(session),
                        model_override=session.model_name,
                        provider_override=session.provider_name,
                        base_url_override=session.base_url,
                        max_steps_override=session.max_steps,
                        approval_mode_override=approval_mode_map[
                            session.approval_policy
                        ],
                        session_id_override=session_id,
                        api_key=None,
                        tape=await self._restore_tape(session.tape_id),
                    )
                    session.tape_id = ctx.tape.tape_id
                    await self._persist_session_async(session)
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

                set_consumer = getattr(adapter, "set_consumer", None)
                if callable(set_consumer):
                    set_consumer(consumer)
                ctx.config["wire_consumer"] = consumer
                await adapter.run_turn(prompt)
                session.tape_id = ctx.tape.tape_id
                await self._persist_session_async(session)
            except Exception as exc:
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
                        turn_id=uuid.uuid4().hex,
                        completion_status=CompletionStatus.ERROR,
                    )
                )
            finally:
                current_task = asyncio.current_task()
                if session.task is None or session.task is not current_task:
                    session.turn_in_progress = False
                session.last_activity = datetime.now()
                await self._persist_session_async(session)

    async def submit_approval(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: Literal["once", "session", "always"] = "once",
    ) -> bool:
        """Submit an approval response for a pending request.

        Uses the session's ApprovalStore to record the response.

        Args:
            session_id: The session ID
            request_id: The approval request ID
            approved: Whether the request is approved
            feedback: Optional feedback message

        Returns:
            True if the response was recorded successfully, False otherwise

        Raises:
            KeyError: If session not found
        """
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)

        # Create approval response and submit to ApprovalStore
        response = ApprovalResponse(
            session_id=session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )
        success = session.approval_coordinator.respond(response)
        session.last_activity = datetime.now()

        if success:
            session.pending_approval = session.approval_coordinator.projection()
            session.approval_response = {
                "decision": "approve" if approved else "deny",
                "feedback": feedback,
            }
            session.approval_event.set()
            await self._persist_session_async(session)
            logger.info(f"Approval submitted for session {session_id}: {approved}")
        else:
            logger.warning(
                f"Approval submission failed for session {session_id}: request {request_id} not found"
            )

        return success

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

    async def ensure_session_runtime(self, session_id: str) -> Any:
        await self._assert_owner(session_id)
        session = await self.get_session_async(session_id)
        if session.runtime_ctx is not None and session.runtime_adapter is not None:
            return session.runtime_ctx

        approval_mode_map = {
            ApprovalPolicy.YOLO: "yolo",
            ApprovalPolicy.INTERACTIVE: "interactive",
            ApprovalPolicy.AUTO: "auto",
        }
        consumer = self._make_session_consumer(session)
        pipeline, ctx = self._create_agent_for_session(
            workspace_root=self._resolve_workspace_root(session),
            model_override=session.model_name,
            provider_override=session.provider_name,
            base_url_override=session.base_url,
            max_steps_override=session.max_steps,
            approval_mode_override=approval_mode_map[session.approval_policy],
            session_id_override=session.id,
            api_key=None,
            tape=await self._restore_tape(session.tape_id),
        )
        ctx.config["wire_consumer"] = consumer
        ctx.config["agent_id"] = ""

        llm_plugin = pipeline._registry.get("llm_provider")
        if session.provider is not None:
            llm_plugin._instance = session.provider

        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
        await adapter.initialize()

        session.runtime_pipeline = pipeline
        session.runtime_ctx = ctx
        session.runtime_adapter = adapter
        session.tape_id = ctx.tape.tape_id
        await self._persist_session_async(session)
        return ctx

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
