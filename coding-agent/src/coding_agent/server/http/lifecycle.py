"""Session manager factory, owner leases, idle cleanup, cloud GC, and lifespan."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI

from coding_agent.stores.local import local_sqlite_path_from_storage_config
from coding_agent.stores.local import local_sqlite_storage_config
from coding_agent.stores.local import normalize_storage_path
from coding_agent.stores.local import storage_has_any_sqlite_backend
from coding_agent.runs import (
    CloudWorkspaceRef,
)
from coding_agent.server.session_manager import SessionManager
from coding_agent.server.stores.session_owner_store import (
    SQLiteSessionOwnerStore,
)
from coding_agent.server.stores.workspace_store import (
    PGWorkspaceMetadataStore,
    WorkspaceRecord,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from coding_agent.server.http.config import (
    _storage_uses_pg_http_sessions,
)
from coding_agent.server.http.workspace_retention import _remote_retention_enabled

logger = logging.getLogger(LOGGER_NAME)


def _cleanup_provisioned_cloud_binding(workspace: CloudWorkspaceRef) -> None:
    cloud_workspace_config = _bindings.module()._load_cloud_workspace_config()
    provider = cloud_workspace_config.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        return
    _bindings.module().cleanup_cloud_binding_from_config(
        cloud_workspace_config, workspace
    )


def _configured_owner_id(storage_config: dict[str, Any]) -> str:
    owner_id = storage_config.get("owner_id")
    if isinstance(owner_id, str) and owner_id.strip():
        return owner_id.strip()
    return f"{socket.gethostname()}:{uuid.uuid4().hex}"


def _configured_fencing_token(storage_config: dict[str, Any]) -> int:
    token = storage_config.get("fencing_token")
    if isinstance(token, int) and token > 0:
        return token
    raise ValueError("storage.fencing_token must be a positive integer")


def _configured_owner_lease_seconds(storage_config: dict[str, Any]) -> float:
    lease_seconds = storage_config.get("owner_lease_seconds", 30.0)
    if not isinstance(lease_seconds, (int, float)):
        raise ValueError("storage.owner_lease_seconds must be numeric")
    if lease_seconds <= 0:
        raise ValueError("storage.owner_lease_seconds must be positive")
    if lease_seconds < 2.0:
        raise ValueError("storage.owner_lease_seconds must be >= 2 seconds")
    return float(lease_seconds)


def _build_session_manager() -> SessionManager:
    storage_config = _bindings.module()._load_storage_config()
    effective_storage_config = storage_config or local_sqlite_storage_config()
    remote_retention_config = _bindings.module()._load_remote_retention_config()
    try:
        _bindings.module()._validate_production_config(
            _bindings.module()._load_server_config(),
            _bindings.module()._load_cloud_workspace_config(),
            storage_config=storage_config,
            remote_retention_config=remote_retention_config,
        )
    except Exception:
        logger.exception("Production config validation failed")
        raise
    has_local_sqlite_intent = storage_has_any_sqlite_backend(effective_storage_config)
    local_sqlite_owner_path = normalize_storage_path(
        str(local_sqlite_path_from_storage_config(effective_storage_config))
    )
    manager = SessionManager(
        storage_config=effective_storage_config,
        owner_store=(
            SQLiteSessionOwnerStore(local_sqlite_owner_path)
            if has_local_sqlite_intent
            else None
        ),
        owner_id=(
            _configured_owner_id(effective_storage_config)
            if has_local_sqlite_intent
            else None
        ),
        fencing_token=1 if has_local_sqlite_intent else None,
        owner_lease_seconds=(
            _configured_owner_lease_seconds(effective_storage_config)
            if has_local_sqlite_intent
            else 30.0
        ),
        cloud_workspace_client_factory=(
            _bindings.module().cloud_client_factory_from_config(
                _bindings.module()._load_cloud_workspace_config()
            )
            if _bindings.module()._load_cloud_workspace_config().get("enabled") is True
            else None
        ),
        provisioned_cloud_binding_cleanup=_bindings.module()._cleanup_provisioned_cloud_binding,
    )
    if remote_retention_config.get("enabled") is True:
        manager.configure_workspace_metadata_store(
            PGWorkspaceMetadataStore(pool=manager.pg_pool)
        )
    if not _storage_uses_pg_http_sessions(effective_storage_config):
        return manager
    owner_store = _bindings.module().SessionOwnerStore(pg_pool=manager.pg_pool)
    manager.configure_owner_leases(
        owner_store=owner_store,
        owner_id=_configured_owner_id(effective_storage_config),
        fencing_token=_configured_fencing_token(effective_storage_config),
        owner_lease_seconds=_configured_owner_lease_seconds(effective_storage_config),
    )
    return manager


async def _renew_owner_leases() -> None:
    if not _bindings.module().session_manager.has_owner_leases_configured:
        return
    while True:
        try:
            await _bindings.module().session_manager.renew_owner_leases()
        except Exception:
            logger.exception("Error renewing owner leases")
        await _bindings.module().asyncio.sleep(
            max(_bindings.module().session_manager.owner_lease_seconds / 2.0, 1.0)
        )


def _cloud_workspace_gc_interval_seconds(
    cloud_workspace_config: dict[str, Any],
) -> float | None:
    if cloud_workspace_config.get("enabled") is not True:
        return None
    interval = cloud_workspace_config.get("gc_interval_seconds")
    max_age = cloud_workspace_config.get("max_workspace_age_seconds")
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or interval <= 0
    ):
        return None
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        return None
    return float(interval)


async def _active_cloud_workspace_ids() -> set[str]:
    active_workspace_ids: set[str] = set()
    for session_id in await _bindings.module().session_manager.list_sessions_async():
        try:
            session = await _bindings.module().session_manager.get_session_async(
                session_id
            )
        except KeyError:
            continue
        target = session.default_run_target
        if target is None:
            continue
        workspace = target.workspace
        if isinstance(workspace, CloudWorkspaceRef):
            active_workspace_ids.add(workspace.workspace_id)
    return active_workspace_ids


async def _cloud_workspace_gc_config() -> dict[str, Any]:
    cloud_workspace_config = dict(_bindings.module()._load_cloud_workspace_config())
    cloud_workspace_config["_active_workspace_ids"] = sorted(
        await _active_cloud_workspace_ids()
    )
    return cloud_workspace_config


def _workspace_record_is_gc_eligible(
    record: WorkspaceRecord,
    *,
    now: datetime,
    active_workspace_ids: set[str],
) -> bool:
    if record.workspace_id in active_workspace_ids:
        return False
    if record.status in {"cleaned", "cleaning", "cleanup_failed", "provisioning"}:
        return False
    if record.retention_policy in {"pinned", "manual"}:
        return False
    if record.retention_policy == "ttl":
        return record.expires_at is not None and record.expires_at <= now
    return record.status == "stale"


async def _cleanup_durable_cloud_workspaces(
    cloud_workspace_config: dict[str, Any],
) -> int:
    provider_instance_id = cloud_workspace_config.get("provider_instance_id")
    if not isinstance(provider_instance_id, str) or not provider_instance_id.strip():
        raise ValueError(
            "cloud_workspace.provider_instance_id is required for durable workspace GC"
        )
    active_workspace_ids = set(cloud_workspace_config.get("_active_workspace_ids", []))
    now = datetime.now(UTC)
    cleaned_count = 0
    for record in await _bindings.module().session_manager.list_workspace_records():
        if record.provider_instance_id != provider_instance_id.strip():
            continue
        if not _workspace_record_is_gc_eligible(
            record,
            now=now,
            active_workspace_ids=active_workspace_ids,
        ):
            continue
        await _bindings.module().session_manager.update_workspace_record_status(
            record.workspace_record_id,
            status="cleaning",
            cleanup_error=None,
        )
        try:
            _ = await _bindings.module().asyncio.to_thread(
                _bindings.module().cleanup_cloud_workspace_from_config,
                cloud_workspace_config,
                record.workspace_id,
                active_workspace_ids=active_workspace_ids,
            )
        except Exception as exc:
            await _bindings.module().session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="cleanup_failed",
                cleanup_error=str(exc) or "workspace cleanup failed",
            )
            logger.exception(
                "Durable workspace GC failed workspace_id=%s",
                record.workspace_id,
            )
            continue
        await _bindings.module().session_manager.update_workspace_record_status(
            record.workspace_record_id,
            status="cleaned",
            cleanup_error=None,
        )
        cleaned_count += 1
    return cleaned_count


async def _cleanup_cloud_workspaces_from_config(
    cloud_workspace_config: dict[str, Any],
) -> int:
    if _remote_retention_enabled():
        return await _cleanup_durable_cloud_workspaces(cloud_workspace_config)
    return await _bindings.module().asyncio.to_thread(
        _bindings.module().cleanup_stale_cloud_workspaces_from_config,
        cloud_workspace_config,
    )


async def _cleanup_cloud_workspaces_on_startup() -> None:
    cloud_workspace_config = await _cloud_workspace_gc_config()
    if cloud_workspace_config.get("enabled") is not True:
        return
    if cloud_workspace_config.get("cleanup_on_startup") is not True:
        return
    try:
        cleaned = await _cleanup_cloud_workspaces_from_config(cloud_workspace_config)
        logger.info("Cloud workspace startup cleanup removed %s workspace(s)", cleaned)
    except Exception:
        logger.exception("Cloud workspace startup cleanup failed")


async def _cleanup_stale_cloud_workspaces_periodically() -> None:
    while True:
        cloud_workspace_config = await _cloud_workspace_gc_config()
        interval = _cloud_workspace_gc_interval_seconds(cloud_workspace_config)
        if interval is None:
            return
        try:
            cleaned = await _cleanup_cloud_workspaces_from_config(
                cloud_workspace_config
            )
            logger.info("Cloud workspace periodic GC removed %s workspace(s)", cleaned)
        except Exception:
            logger.exception("Cloud workspace periodic GC failed")
        await _bindings.module().asyncio.sleep(interval)


async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    # Startup
    _bindings.module()._log_development_mode_warning(
        _bindings.module()._load_server_config()
    )
    await _bindings.module()._cleanup_cloud_workspaces_on_startup()
    try:
        await _bindings.module().session_manager.backfill_owner_leases()
    except Exception:
        logger.exception("Failed to backfill owner leases during startup")
    try:
        recovered = (
            await _bindings.module().session_manager.recover_stale_runtime_runs()
        )
        if recovered:
            logger.info("Recovered %s stale runtime run(s)", recovered)
    except Exception:
        logger.exception("Failed to recover stale runtime runs during startup")
    cleanup_task = asyncio.create_task(_bindings.module()._cleanup_idle_sessions())
    owner_renew_task = asyncio.create_task(_bindings.module()._renew_owner_leases())
    cloud_workspace_gc_task = asyncio.create_task(
        _bindings.module()._cleanup_stale_cloud_workspaces_periodically()
    )
    logger.info("HTTP server starting up")

    try:
        yield  # Server runs here
    finally:
        cloud_workspace_gc_task.cancel()
        try:
            await cloud_workspace_gc_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Cloud workspace GC task failed during shutdown")
        owner_renew_task.cancel()
        try:
            await owner_renew_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Owner lease renewal task failed during shutdown")

    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Close all sessions
    try:
        for (
            session_id
        ) in await _bindings.module().session_manager.list_sessions_async():
            try:
                await _bindings.module().session_manager.shutdown_session_runtime(
                    session_id,
                    interrupt_active_turn=True,
                )
            except Exception:
                logger.warning(
                    "Failed to shut down runtime for session %s during server shutdown",
                    session_id,
                    exc_info=True,
                )
    finally:
        try:
            await _bindings.module().session_manager.release_owned_sessions()
        finally:
            await _bindings.module().session_manager.close()

    logger.info("HTTP server shut down")


async def _cleanup_idle_sessions() -> None:
    """Background task to clean up idle sessions."""
    while True:
        await _bindings.module().asyncio.sleep(60)  # Check every minute
        try:
            await _bindings.module().session_manager.cleanup_idle_sessions(
                _bindings.module().SESSION_IDLE_TIMEOUT_MINUTES
            )
        except Exception:
            logger.exception("Error during idle session cleanup")


__all__ = [
    "_active_cloud_workspace_ids",
    "_build_session_manager",
    "_cleanup_cloud_workspaces_from_config",
    "_cleanup_cloud_workspaces_on_startup",
    "_cleanup_durable_cloud_workspaces",
    "_cleanup_idle_sessions",
    "_cleanup_provisioned_cloud_binding",
    "_cleanup_stale_cloud_workspaces_periodically",
    "_cloud_workspace_gc_config",
    "_cloud_workspace_gc_interval_seconds",
    "_configured_fencing_token",
    "_configured_owner_id",
    "_configured_owner_lease_seconds",
    "_renew_owner_leases",
    "_workspace_record_is_gc_eligible",
    "lifespan",
]
