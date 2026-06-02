"""Local CLI runtime/session boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from typing import Any, Protocol, runtime_checkable

from coding_agent.adapter import PipelineAdapter
from coding_agent.core.config import Config
from coding_agent.server.session_manager import SessionManager


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


@runtime_checkable
class LocalCliSessionManager(Protocol):
    """Subset of session behavior used by the interactive local REPL."""

    async def create_session(self, **kwargs: Any) -> str: ...

    def get_session(self, session_id: str) -> Any: ...

    async def get_session_async(self, session_id: str) -> Any: ...

    async def list_sessions_async(self) -> list[str]: ...

    async def list_checkpoints(self, session_id: str) -> Any: ...

    async def session_resume_metadata(self, session_id: str) -> dict[str, object]: ...

    async def ensure_session_runtime(self, session_id: str) -> Any: ...

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str,
    ) -> Any: ...

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None: ...

    async def run_agent(self, session_id: str, prompt: str) -> None: ...

    async def resume_session(self, session_id: str, **kwargs: Any) -> Any: ...

    def attach_runtime(
        self,
        managed_session: Any,
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
        default_factory=lambda: {
            "http_session_backend": "fs",
            "runtime_backend": "jsonl",
        }
    )

    def __post_init__(self) -> None:
        self._delegate = SessionManager(storage_config=dict(self.storage_config))

    async def create_session(self, **kwargs: Any) -> str:
        return await self._delegate.create_session(**kwargs)

    def get_session(self, session_id: str) -> Any:
        return self._delegate.get_session(session_id)

    async def get_session_async(self, session_id: str) -> Any:
        return await self._delegate.get_session_async(session_id)

    async def list_sessions_async(self) -> list[str]:
        return await self._delegate.list_sessions_async()

    async def list_checkpoints(self, session_id: str) -> Any:
        return await self._delegate.list_checkpoints(session_id)

    async def session_resume_metadata(self, session_id: str) -> dict[str, object]:
        return await self._delegate.session_resume_metadata(session_id)

    async def ensure_session_runtime(self, session_id: str) -> Any:
        return await self._delegate.ensure_session_runtime(session_id)

    async def replace_session_runtime_config(
        self,
        session_id: str,
        *,
        model_name: str,
    ) -> Any:
        return await self._delegate.replace_session_runtime_config(
            session_id,
            model_name=model_name,
        )

    async def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> None:
        await self._delegate.restore_checkpoint(session_id, checkpoint_id)

    async def run_agent(self, session_id: str, prompt: str) -> None:
        await self._delegate.run_agent(session_id, prompt)

    async def resume_session(self, session_id: str, **kwargs: Any) -> Any:
        return await self._delegate.resume_session(session_id, **kwargs)

    def attach_runtime(
        self,
        managed_session: Any,
        *,
        pipeline: Any,
        pipeline_ctx: Any,
        pipeline_adapter: Any,
    ) -> None:
        managed_session.runtime_pipeline = pipeline
        managed_session.runtime_ctx = pipeline_ctx
        managed_session.runtime_adapter = pipeline_adapter
        managed_session.tape_id = pipeline_ctx.tape.tape_id
        self._delegate._persist_session(managed_session)

    async def close(self) -> None:
        await self._delegate.close()


def create_local_cli_session_manager(
    *,
    storage_config: dict[str, object] | None = None,
) -> LocalCliSessionManager:
    """Create the local REPL session manager.

    The concrete implementation remains the existing managed local session
    service for now. Keeping this construction here prevents REPL code from
    importing server/control-plane types directly.
    """

    if storage_config is None:
        return ServerBackedLocalCliSessionManager()
    return ServerBackedLocalCliSessionManager(storage_config=storage_config)


__all__ = [
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
