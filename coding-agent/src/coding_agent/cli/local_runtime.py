"""Local CLI runtime/session boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import importlib
import logging
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from coding_agent.adapter import PipelineAdapter
from coding_agent.core.config import Config
from coding_agent.local_storage import local_sqlite_storage_config
from coding_agent.local_storage import local_sqlite_path_from_storage_config
from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_owner_store import SQLiteSessionOwnerStore

logger = logging.getLogger(__name__)


def local_cli_session_origin(*, entrypoint: str, mode: str) -> dict[str, str]:
    """Build durable origin metadata for sessions created by local CLI surfaces."""
    if not entrypoint.strip():
        raise ValueError("entrypoint must be non-empty")
    if not mode.strip():
        raise ValueError("mode must be non-empty")
    return {
        "channel": "local_cli",
        "entrypoint": entrypoint,
        "mode": mode,
    }


@dataclass(frozen=True)
class LocalCliRuntime:
    """In-process runtime components for a local CLI session."""

    pipeline: Any
    pipeline_ctx: Any
    pipeline_adapter: Any


class LocalCliManagedSession(Protocol):
    tape_id: str | None

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None: ...


@runtime_checkable
class LocalCliSessionManager(Protocol):
    """Subset of session behavior used by the interactive local REPL."""

    async def create_session(self, **kwargs: Any) -> str: ...

    def get_session(self, session_id: str) -> Any: ...

    async def get_session_async(self, session_id: str) -> Any: ...

    async def acquire_session_owner(self, session_id: str) -> None: ...

    async def start_owner_lease_renewal(self) -> None: ...

    async def list_sessions_async(self) -> list[str]: ...

    async def close_session(self, session_id: str) -> None: ...

    async def update_session_mcp_servers(
        self,
        session_id: str,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> None: ...

    async def list_runtime_runs(self, session_id: str) -> Any: ...

    async def list_checkpoints(self, session_id: str) -> Any: ...

    async def session_resume_metadata(self, session_id: str) -> dict[str, object]: ...

    async def replay_display_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> Any: ...

    async def ensure_session_runtime(self, session_id: str) -> Any: ...

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str,
        provider_name: str | None = None,
        base_url: str | None = None,
    ) -> Any: ...

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None: ...

    async def run_agent(self, session_id: str, prompt: str) -> None: ...

    async def cancel_session_turn(self, session_id: str) -> Any: ...

    async def submit_approval_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: str = "once",
    ) -> Any: ...

    async def resume_session(self, session_id: str, **kwargs: Any) -> Any: ...

    async def attach_runtime(
        self,
        managed_session: LocalCliManagedSession,
        *,
        pipeline: Any,
        pipeline_ctx: Any,
        pipeline_adapter: Any,
    ) -> None: ...

    async def close(self) -> None: ...


@dataclass
class ServerBackedLocalCliSessionManager:
    """Local CLI runtime adapter backed by the existing session service."""

    storage_config: dict[str, object] = field(
        default_factory=local_sqlite_storage_config
    )
    owner_store: Any | None = None
    owner_id: str | None = None
    fencing_token: int | None = None
    owner_renew_interval_seconds: float = 10.0
    _owner_renew_task: asyncio.Task[None] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.owner_renew_interval_seconds <= 0:
            raise ValueError("owner_renew_interval_seconds must be positive")
        owner_store = self.owner_store
        owner_id = self.owner_id
        fencing_token = self.fencing_token
        if owner_store is None:
            owner_store = SQLiteSessionOwnerStore(
                local_sqlite_path_from_storage_config(
                    dict(self.storage_config),
                )
            )
            owner_id = owner_id or f"local-cli:{uuid4().hex}"
            fencing_token = fencing_token or 1
        self.owner_store = owner_store
        self.owner_id = owner_id
        self.fencing_token = fencing_token
        self._delegate = SessionManager(
            storage_config=dict(self.storage_config),
            owner_store=owner_store,
            owner_id=owner_id,
            fencing_token=fencing_token,
        )

    async def create_session(self, **kwargs: Any) -> str:
        return await self._delegate.create_session(**kwargs)

    def get_session(self, session_id: str) -> Any:
        return self._delegate.get_session(session_id)

    async def get_session_async(self, session_id: str) -> Any:
        return await self._delegate.get_session_async(session_id)

    async def acquire_session_owner(self, session_id: str) -> None:
        await self._delegate.acquire_session_owner(session_id)

    async def start_owner_lease_renewal(self) -> None:
        if self.owner_store is None:
            return
        if self._owner_renew_task is not None:
            return
        self._owner_renew_task = asyncio.create_task(self._renew_owner_leases_loop())

    async def _renew_owner_leases_loop(self) -> None:
        while True:
            try:
                await self._delegate.renew_owner_leases()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error renewing local CLI owner leases")
            await asyncio.sleep(self.owner_renew_interval_seconds)

    async def list_sessions_async(self) -> list[str]:
        return await self._delegate.list_sessions_async()

    async def close_session(self, session_id: str) -> None:
        await self._delegate.close_session(session_id)

    async def update_session_mcp_servers(
        self,
        session_id: str,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> None:
        await self._delegate.update_session_mcp_servers(session_id, mcp_servers)

    async def update_session_additional_directories(
        self,
        session_id: str,
        additional_directories: list[str],
    ) -> None:
        await self._delegate.update_session_additional_directories(
            session_id,
            additional_directories,
        )

    async def list_runtime_runs(self, session_id: str) -> Any:
        return await self._delegate.list_runtime_runs(session_id)

    async def list_checkpoints(self, session_id: str) -> Any:
        return await self._delegate.list_checkpoints(session_id)

    async def session_resume_metadata(self, session_id: str) -> dict[str, object]:
        return await self._delegate.session_resume_metadata(session_id)

    async def replay_display_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> Any:
        return await self._delegate.replay_display_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )

    async def ensure_session_runtime(self, session_id: str) -> Any:
        return await self._delegate.ensure_session_runtime(session_id)

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str,
        provider_name: str | None = None,
        base_url: str | None = None,
    ) -> Any:
        return await self._delegate.replace_session_runtime_config(
            session_id,
            model_name=model_name,
            provider_name=provider_name,
            base_url=base_url,
        )

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        await self._delegate.restore_checkpoint(session_id, checkpoint_id)

    async def run_agent(self, session_id: str, prompt: str) -> None:
        await self._delegate.run_agent(session_id, prompt)

    async def cancel_session_turn(self, session_id: str) -> Any:
        return await self._delegate.cancel_session_turn(session_id)

    async def submit_approval_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: str = "once",
    ) -> Any:
        return await self._delegate.submit_approval_response(
            session_id=session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )

    async def resume_session(self, session_id: str, **kwargs: Any) -> Any:
        return await self._delegate.resume_session(session_id, **kwargs)

    async def attach_runtime(
        self,
        managed_session: LocalCliManagedSession,
        *,
        pipeline: Any,
        pipeline_ctx: Any,
        pipeline_adapter: Any,
    ) -> None:
        managed_session.attach_runtime_binding(
            pipeline=pipeline,
            ctx=pipeline_ctx,
            adapter=pipeline_adapter,
        )
        managed_session.tape_id = pipeline_ctx.tape.tape_id
        await self._delegate._persist_session_async(managed_session)

    async def close(self) -> None:
        if self._owner_renew_task is not None:
            self._owner_renew_task.cancel()
            try:
                await self._owner_renew_task
            except asyncio.CancelledError:
                pass
            self._owner_renew_task = None
        await self._delegate.release_owned_sessions()
        await self._delegate.close()


def create_local_cli_session_manager(
    *,
    storage_config: dict[str, object] | None = None,
    owner_store: Any | None = None,
    owner_id: str | None = None,
    fencing_token: int | None = None,
) -> LocalCliSessionManager:
    """Create the local REPL session manager.

    The concrete implementation remains the existing managed local session
    service for now. Keeping this construction here prevents REPL code from
    importing server/control-plane types directly.
    """

    kwargs = {
        "owner_store": owner_store,
        "owner_id": owner_id,
        "fencing_token": fencing_token,
    }
    if storage_config is None:
        return ServerBackedLocalCliSessionManager(**kwargs)
    return ServerBackedLocalCliSessionManager(
        storage_config=storage_config,
        **kwargs,
    )


__all__ = [
    "LocalCliManagedSession",
    "LocalCliSessionManager",
    "LocalCliRuntime",
    "ServerBackedLocalCliSessionManager",
    "create_local_cli_runtime",
    "create_local_cli_session_manager",
    "local_cli_session_origin",
]


def create_agent(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
    return importlib.import_module("coding_agent.app").create_agent(*args, **kwargs)


def create_local_cli_runtime(config: Config, consumer: Any) -> LocalCliRuntime:
    """Compose the in-process agent runtime used by local CLI products."""

    pipeline, pipeline_ctx = create_agent(
        api_key=str(config.api_key.get_secret_value()) if config.api_key else None,
        model_override=config.model,
        provider_override=config.provider,
        base_url_override=config.base_url,
        workspace_root=config.repo,
        max_steps_override=config.max_steps,
        approval_mode_override=config.approval_mode,
    )
    pipeline_adapter = PipelineAdapter(
        pipeline=pipeline,
        ctx=pipeline_ctx,
        consumer=consumer,
    )
    pipeline_ctx.config["wire_consumer"] = consumer
    pipeline_ctx.config["agent_id"] = ""
    return LocalCliRuntime(
        pipeline=pipeline,
        pipeline_ctx=pipeline_ctx,
        pipeline_adapter=pipeline_adapter,
    )
