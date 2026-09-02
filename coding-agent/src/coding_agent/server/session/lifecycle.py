"""Session create/remove/close/shutdown and idle cleanup."""

from __future__ import annotations

import logging
import asyncio
import uuid
from datetime import (
    UTC,
    datetime,
)
from inspect import isawaitable
from pathlib import Path
from typing import (
    Any,
    cast,
)
from coding_agent.approval import (
    ApprovalCoordinator,
    ApprovalPolicy,
)
from coding_agent.runtime_activation import version_for_new_session
from coding_agent.approval.store import ApprovalStore
from coding_agent.core import config as core_config
from coding_agent.stores.runtime_store import JSONObject
from coding_agent.runs import RunTarget
from coding_agent.server.session.models import Session
from coding_agent.server.session.models import _local_default_run_target
from coding_agent.server.session.records import (
    _session_additional_directories_from_store,
)
from coding_agent.server.session.records import _session_mcp_servers_from_store

logger = logging.getLogger("coding_agent.server.session_manager")

GRACEFUL_SHUTDOWN_INTERRUPTED_RUN_ERROR = (
    "runtime run was interrupted during graceful shutdown"
)
GRACEFUL_SHUTDOWN_RECOVERY_REASON = "graceful_shutdown"
GRACEFUL_SHUTDOWN_INTERRUPTABLE_RUN_STATUSES = frozenset(
    {"running", "cancelling", "cancelled"}
)


class LifecycleOps:
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
        api_key: str | None = None,
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
            api_key: Process-local provider API key; never persisted
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
        resolved_api_key = (
            None
            if provider_name == "codex"
            or (provider_name is not None and provider_name.startswith("codex:"))
            else api_key or None
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
            api_key=resolved_api_key,
            max_steps=max_steps,
            mcp_servers=resolved_mcp_servers,
            additional_directories=resolved_additional_directories,
            task=None,
        )

        async with self._lock:
            try:
                await self._acquire_owner_for_session(session_id)
                store = self._authoritative_store()
                if store is not None:
                    activation = await store.load_runtime_activation()
                    session.runtime_version = version_for_new_session(activation)
                await self._persist_session_async(session)
                await self._persist_workspace_record_for_session(session)
                if store is not None:
                    await store.snapshot_chat_events(session_id, None, 1)
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

            await self._stop_connected_chat_tasks(session_id)
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
                else None
            )

            if (
                interrupted_run_id is not None
                and self.can_settle_root_run_authoritatively()
            ):
                from coding_agent.events.connected_chat import (
                    RootRunAlreadySettledError,
                )

                try:
                    await self.settle_root_run(
                        session_id,
                        run_id=interrupted_run_id,
                        outcome="interrupted",
                        error=GRACEFUL_SHUTDOWN_INTERRUPTED_RUN_ERROR,
                    )
                except RootRunAlreadySettledError:
                    pass

            await self._stop_connected_chat_tasks(session_id)
            await self._runtime_control_services.task_stopper().stop(
                session_id=session_id,
                task=session.task,
            )
            if (
                interrupted_run_id is not None
                and not self.can_settle_root_run_authoritatively()
            ):
                await self._mark_graceful_shutdown_interrupted_run(interrupted_run_id)

            await self._runtime_closer.close(session)
            session.task = None
            session.turn_in_progress = False
            await self._persist_session_async(session)

    async def _stop_connected_chat_tasks(self, session_id: str) -> None:
        from coding_agent.events.connected_chat import RootRunAlreadySettledError

        run_ids = list(self._chat_runs_by_session.get(session_id, ()))
        for run_id in run_ids:
            task = self._chat_run_tasks.pop(run_id, None)
            if task is None or task.done():
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except RootRunAlreadySettledError:
                pass
        self._chat_runs_by_session.pop(session_id, None)

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

    async def close(self) -> None:
        await self._close_resource_async(self._store)
        if self._owns_pg_pool:
            await self._close_resource_async(self._pg_pool)

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
