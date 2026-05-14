"""FastAPI-based HTTP server for Coding Agent with REST endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agentkit.config.loader import load_config as load_agent_toml
from agentkit.errors import ConfigError
from agentkit.tape.extract import ToolCallRecord, TurnTrace, extract_turns
from agentkit.tape.tape import Tape
from coding_agent.approval import ApprovalPolicy
from coding_agent.environment import (
    cleanup_cloud_binding_from_config,
    cleanup_cloud_workspace_from_config,
    cleanup_stale_cloud_workspaces_from_config,
    cloud_client_factory_from_config,
    cloud_workspace_ready_from_config,
    WorkspaceArchiveManifest,
    WorkspaceBranchPublication,
    WorkspaceInventoryEntry,
    export_workspace_archive_by_id_from_config,
    export_workspace_archive_from_config,
    get_cloud_workspace_from_config,
    list_cloud_workspaces_from_config,
    publish_workspace_branch_from_config,
    provision_cloud_binding_from_config,
    workspace_archive_manifest_from_config,
    workspace_diff_from_config,
    workspace_patch_from_config,
)
from coding_agent.ui.binding_resolver import DefaultBindingResolver
from coding_agent.ui.session_manager import Session, SessionManager
from coding_agent.ui.session_owner_store import (
    SessionOwnerStore,
    SessionOwnershipConflictError,
    SessionOwnershipConflictReason,
)
from coding_agent.ui.workspace_store import PGWorkspaceMetadataStore
from coding_agent.ui.workspace_store import WorkspaceRecord
from coding_agent.ui.schemas import (
    PromptRequest,
    CreateSessionRequest,
    ApproveRequest,
    CheckpointCaptureRequest,
    SessionResponse,
    CheckpointListResponse,
    CheckpointMetadataResponse,
    CheckpointRestoreResponse,
    ApprovalResponseSchema,
    CancelSessionResponse,
    CloseSessionResponse,
    HealthResponse,
    ReadinessResponse,
    SessionListResponse,
    SessionResultResponse,
    SessionSummaryResponse,
    PublishSessionRequest,
    PublishSessionResponse,
    WorkspaceArchiveResponse,
    WorkspaceArchiveManifestResponse,
    WorkspaceCleanupResponse,
    WorkspaceDiffResponse,
    WorkspaceDiffFileSchema,
    WorkspaceGcResponse,
    WorkspaceListResponse,
    WorkspacePatchResponse,
    WorkspaceRetentionRequest,
    WorkspaceRetentionResponse,
    WorkspaceSummarySchema,
    WorkspaceUnpinRequest,
)
from coding_agent.ui.auth import AuthContext, auth_context_from_headers, verify_api_key
from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    ExecutionBinding,
    LocalExecutionBinding,
)
from coding_agent.ui.rate_limit import limiter, RateLimits
from slowapi.errors import RateLimitExceeded
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    ErrorMessage,
    LocalWire,
    StepInfo,
    StreamDelta,
    ToolCallBegin,
    ToolCallDelta,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireMessage,
)
from coding_agent.wire.protocol import ToolResultDelta
from coding_agent.wire.protocol import ThinkingDelta, TurnStatusDelta

logger = logging.getLogger(__name__)

# Constants
APPROVAL_TIMEOUT_SECONDS = 120

_SESSION_RESULT_TOOL_DETAIL_KEYS = (
    "command",
    "path",
    "file_path",
    "pattern",
)
_SESSION_RESULT_MAX_TOOL_ITEMS = 5
_SESSION_RESULT_MAX_TOOL_DETAIL_CHARS = 160
SESSION_IDLE_TIMEOUT_MINUTES = 30
_SERVER_CONFIG_ENV = "CODING_AGENT_SERVER_CONFIG"
_GITHUB_API_BASE_URL = "https://api.github.com"
_GITHUB_API_VERSION = "2022-11-28"
_GITHUB_SCP_REMOTE_RE = re.compile(r"^git@github\.com:(?P<path>[^:]+)$")


class GitHubPrUnsupportedError(ValueError):
    pass


class GitHubPrPublicationError(Exception):
    pass


def _server_config_path() -> Path:
    configured_path = os.environ.get(_SERVER_CONFIG_ENV)
    if configured_path is not None and configured_path.strip():
        return Path(configured_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "agent.toml"


def _has_explicit_server_config() -> bool:
    configured_path = os.environ.get(_SERVER_CONFIG_ENV)
    return configured_path is not None and bool(configured_path.strip())


def _load_agent_config_section(section: str) -> dict[str, Any]:
    config_path = _server_config_path()
    try:
        return cast(
            dict[str, Any],
            load_agent_toml(config_path).extra.get(section, {}),
        )
    except (ConfigError, OSError) as exc:
        if _has_explicit_server_config():
            raise
        if isinstance(exc, ConfigError):
            detail = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
            if not detail.startswith("config file not found:"):
                raise
        logger.warning(
            "Unable to load %s config from %s; using defaults",
            section,
            config_path,
            exc_info=True,
        )
        return {}


def _load_storage_config() -> dict[str, Any]:
    return _load_agent_config_section("storage")


def _load_cloud_workspace_config() -> dict[str, Any]:
    cloud_workspace_config = _load_agent_config_section("cloud_workspace")
    runtime_profiles = _load_runtime_profiles_config()
    if runtime_profiles:
        cloud_workspace_config = dict(cloud_workspace_config)
        cloud_workspace_config["runtime_profiles"] = runtime_profiles
    remote_sources = _load_remote_sources_config()
    if remote_sources:
        cloud_workspace_config = dict(cloud_workspace_config)
        cloud_workspace_config["remote_sources"] = remote_sources
    remote_phases = _load_remote_phases_config()
    if remote_phases:
        cloud_workspace_config = dict(cloud_workspace_config)
        cloud_workspace_config["remote_phases"] = remote_phases
    return cloud_workspace_config


def _load_runtime_profiles_config() -> dict[str, Any]:
    return _load_agent_config_section("runtime_profiles")


def _load_remote_publication_config() -> dict[str, Any]:
    return _load_agent_config_section("remote_publication")


def _load_remote_retention_config() -> dict[str, Any]:
    return _load_agent_config_section("remote_retention")


def _load_remote_sources_config() -> dict[str, Any]:
    return _load_agent_config_section("remote_sources")


def _load_remote_phases_config() -> dict[str, Any]:
    return _load_agent_config_section("remote_phases")


def _load_server_config() -> dict[str, Any]:
    return _load_agent_config_section("server")


def _load_agent_runtime_defaults() -> dict[str, Any]:
    config_path = _server_config_path()
    try:
        config = load_agent_toml(config_path)
    except (ConfigError, OSError) as exc:
        if _has_explicit_server_config():
            raise
        if isinstance(exc, ConfigError):
            detail = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
            if not detail.startswith("config file not found:"):
                raise
        return {}
    defaults: dict[str, Any] = {
        "provider": config.provider,
        "model": config.model,
        "max_steps": config.max_turns,
    }
    return defaults


def _optional_config_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _config_int_or_default(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _require_positive_int(config: dict[str, Any], key: str) -> None:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"cloud_workspace.{key} must be a positive integer")


def _require_non_empty_string(config: dict[str, Any], key: str) -> None:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"cloud_workspace.{key} must be configured")


def _require_positive_int_field(
    config: dict[str, Any],
    *,
    section: str,
    key: str,
) -> None:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{section}.{key} must be a positive integer")


def _require_string_list_field(
    config: dict[str, Any],
    *,
    section: str,
    key: str,
) -> None:
    value = config.get(key)
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError(f"{section}.{key} must be a list of strings")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{section}.{key} must be a list of non-empty strings")


def _is_root_exec_user(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "root":
        return True
    user = normalized.split(":", 1)[0]
    return user in {"0", "root"}


def _validate_production_remote_phases(
    cloud_workspace_config: dict[str, Any],
) -> None:
    remote_phases = cloud_workspace_config.get("remote_phases")
    if remote_phases is None:
        return
    if not isinstance(remote_phases, dict):
        raise ValueError("remote_phases must be a table")

    setup_phase = remote_phases.get("setup")
    if setup_phase is not None:
        if not isinstance(setup_phase, dict):
            raise ValueError("remote_phases.setup must be a table")
        allow_request_commands = setup_phase.get("allow_request_commands")
        if allow_request_commands is not None and not isinstance(
            allow_request_commands, bool
        ):
            raise ValueError(
                "remote_phases.setup.allow_request_commands must be a boolean"
            )
        if allow_request_commands is True:
            raise ValueError(
                "remote_phases.setup.allow_request_commands=true requires "
                "request-provided setup command execution support"
            )
        if setup_phase.get("enabled") is True:
            if setup_phase.get("network") not in {"none", "bridge"}:
                raise ValueError(
                    'remote_phases.setup.network must be "none" or "bridge"'
                )
            _require_positive_int_field(
                setup_phase,
                section="remote_phases.setup",
                key="timeout_seconds",
            )
            _require_string_list_field(
                setup_phase,
                section="remote_phases.setup",
                key="commands",
            )
            _require_string_list_field(
                setup_phase,
                section="remote_phases.setup",
                key="secret_env_allowlist",
            )
            commands = setup_phase.get("commands")
            has_configured_commands = isinstance(commands, list) and len(commands) > 0
            if not has_configured_commands:
                raise ValueError(
                    "remote_phases.setup.enabled=true requires non-empty "
                    "server-configured commands"
                )

    agent_phase = remote_phases.get("agent")
    if agent_phase is not None:
        if not isinstance(agent_phase, dict):
            raise ValueError("remote_phases.agent must be a table")
        if agent_phase.get("network") != "none":
            raise ValueError('remote_phases.agent.network must be "none"')
        timeout_seconds = agent_phase.get("timeout_seconds")
        if timeout_seconds is not None:
            _require_positive_int_field(
                agent_phase,
                section="remote_phases.agent",
                key="timeout_seconds",
            )
        _require_string_list_field(
            agent_phase,
            section="remote_phases.agent",
            key="secret_env_allowlist",
        )


def _validate_production_remote_retention(
    cloud_workspace_config: dict[str, Any],
    storage_config: dict[str, Any],
    remote_retention_config: dict[str, Any],
) -> None:
    enabled = remote_retention_config.get("enabled")
    if enabled is None or enabled is False:
        return
    if enabled is not True:
        raise ValueError("remote_retention.enabled must be a boolean")

    provider_instance_id = cloud_workspace_config.get("provider_instance_id")
    if not isinstance(provider_instance_id, str) or not provider_instance_id.strip():
        raise ValueError(
            "cloud_workspace.provider_instance_id is required when "
            "remote_retention.enabled=true"
        )

    workspace_host_label = cloud_workspace_config.get("workspace_host_label")
    if workspace_host_label is not None and (
        not isinstance(workspace_host_label, str) or not workspace_host_label.strip()
    ):
        raise ValueError("cloud_workspace.workspace_host_label must be a string")

    if not _storage_uses_pg_http_sessions(storage_config):
        raise ValueError(
            "remote_retention.enabled=true requires PostgreSQL HTTP session storage"
        )

    default_policy = remote_retention_config.get("default_policy")
    if default_policy not in {"delete_on_close", "ttl", "pinned", "manual"}:
        raise ValueError("remote_retention.default_policy is required")
    if default_policy == "ttl":
        _require_positive_int_field(
            remote_retention_config,
            section="remote_retention",
            key="default_ttl_seconds",
        )

    allow_user_pin = remote_retention_config.get("allow_user_pin")
    if not isinstance(allow_user_pin, bool):
        raise ValueError(
            "remote_retention.allow_user_pin is required when remote retention "
            "is enabled and must be a boolean"
        )


def _validate_production_config(
    server_config: dict[str, Any],
    cloud_workspace_config: dict[str, Any],
    *,
    storage_config: dict[str, Any] | None = None,
    remote_retention_config: dict[str, Any] | None = None,
) -> None:
    if server_config.get("production") is not True:
        return

    bearer_token = server_config.get("bearer_token")
    bearer_token_env = server_config.get("bearer_token_env")
    if isinstance(bearer_token_env, str) and bearer_token_env.strip():
        token = os.environ.get(bearer_token_env.strip())
        if token is None or not token.strip():
            raise ValueError(
                "server.bearer_token_env must reference a non-empty environment variable"
            )
    elif not isinstance(bearer_token, str) or not bearer_token.strip():
        raise ValueError("server.bearer_token_env or server.bearer_token is required")

    if cloud_workspace_config.get("enabled") is not True:
        raise ValueError("production requires cloud_workspace.enabled=true")
    if cloud_workspace_config.get("provider") != "docker":
        raise ValueError('production requires cloud_workspace.provider="docker"')

    image_allowlist = cloud_workspace_config.get("image_allowlist")
    if not isinstance(image_allowlist, list) or not image_allowlist:
        raise ValueError(
            "cloud_workspace.image_allowlist must be explicitly configured"
        )
    for image in image_allowlist:
        if not isinstance(image, str) or not image.strip():
            raise ValueError("cloud_workspace.image_allowlist must contain strings")
    default_runtime_profile = cloud_workspace_config.get("default_runtime_profile")
    runtime_profiles = cloud_workspace_config.get("runtime_profiles")
    if (
        not isinstance(default_runtime_profile, str)
        or not default_runtime_profile.strip()
    ):
        raise ValueError("cloud_workspace.default_runtime_profile is required")
    if not isinstance(runtime_profiles, dict) or not runtime_profiles:
        raise ValueError("runtime_profiles must be explicitly configured")
    default_profile = runtime_profiles.get(default_runtime_profile.strip())
    if not isinstance(default_profile, dict):
        raise ValueError(
            "cloud_workspace.default_runtime_profile must refer to a configured runtime profile"
        )
    default_profile_image = default_profile.get("image")
    if not isinstance(default_profile_image, str) or not default_profile_image.strip():
        raise ValueError(
            f"runtime_profiles.{default_runtime_profile.strip()}.image is required"
        )
    if default_profile_image.strip() not in image_allowlist:
        raise ValueError(
            "cloud_workspace.default_runtime_profile image must be in image_allowlist"
        )

    exec_user = cloud_workspace_config.get("exec_user")
    if not isinstance(exec_user, str) or not exec_user.strip():
        raise ValueError("cloud_workspace.exec_user must be explicitly configured")
    if _is_root_exec_user(exec_user):
        raise ValueError("cloud_workspace.exec_user must not be root")

    _require_positive_int(cloud_workspace_config, "max_active_workspaces")
    _require_positive_int(cloud_workspace_config, "max_workspace_age_seconds")
    _require_positive_int(cloud_workspace_config, "gc_interval_seconds")
    _require_non_empty_string(cloud_workspace_config, "cpus")
    _require_non_empty_string(cloud_workspace_config, "memory")
    _require_positive_int(cloud_workspace_config, "pids_limit")

    if cloud_workspace_config.get("network") != "none":
        raise ValueError('cloud_workspace.network must be "none"')
    _validate_production_remote_phases(cloud_workspace_config)
    _validate_production_remote_retention(
        cloud_workspace_config,
        storage_config or {},
        remote_retention_config or {},
    )


def _log_development_mode_warning(server_config: dict[str, Any]) -> None:
    if server_config.get("production") is True:
        return
    logger.warning(
        "Running in development mode. This configuration is not safe for team production use."
    )


def _build_binding_resolver() -> DefaultBindingResolver:
    cloud_workspace_config = _load_cloud_workspace_config()
    if cloud_workspace_config.get("enabled") is not True:
        return DefaultBindingResolver()
    return DefaultBindingResolver(
        cloud_client_factory=cloud_client_factory_from_config(cloud_workspace_config)
    )


def _cleanup_provisioned_cloud_binding(binding: CloudWorkspaceBinding) -> None:
    cloud_workspace_config = _load_cloud_workspace_config()
    provider = cloud_workspace_config.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        return
    cleanup_cloud_binding_from_config(cloud_workspace_config, binding)


def _storage_uses_pg_http_sessions(storage_config: dict[str, Any]) -> bool:
    http_backend = storage_config.get("http_session_backend")
    if http_backend is not None:
        return isinstance(http_backend, str) and http_backend.strip().lower() == "pg"

    session_backend = storage_config.get("session_backend")
    if session_backend is not None:
        return (
            isinstance(session_backend, str) and session_backend.strip().lower() == "pg"
        )

    tape_backend = str(storage_config.get("tape_backend", "jsonl")).strip().lower()
    return tape_backend == "pg"


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
    storage_config = _load_storage_config()
    remote_retention_config = _load_remote_retention_config()
    try:
        _validate_production_config(
            _load_server_config(),
            _load_cloud_workspace_config(),
            storage_config=storage_config,
            remote_retention_config=remote_retention_config,
        )
    except Exception:
        logger.exception("Production config validation failed")
        raise
    manager = SessionManager(
        storage_config=storage_config,
        binding_resolver=_build_binding_resolver(),
        provisioned_cloud_binding_cleanup=_cleanup_provisioned_cloud_binding,
    )
    if remote_retention_config.get("enabled") is True:
        manager.configure_workspace_metadata_store(
            PGWorkspaceMetadataStore(pool=manager.pg_pool)
        )
    if not _storage_uses_pg_http_sessions(storage_config):
        return manager
    owner_store = SessionOwnerStore(pg_pool=manager.pg_pool)
    manager.configure_owner_leases(
        owner_store=owner_store,
        owner_id=_configured_owner_id(storage_config),
        fencing_token=_configured_fencing_token(storage_config),
        owner_lease_seconds=_configured_owner_lease_seconds(storage_config),
    )
    return manager


async def _renew_owner_leases() -> None:
    if not session_manager.has_owner_leases_configured:
        return
    while True:
        try:
            await session_manager.renew_owner_leases()
        except Exception:
            logger.exception("Error renewing owner leases")
        await asyncio.sleep(max(session_manager.owner_lease_seconds / 2.0, 1.0))


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
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        binding = session.execution_binding
        if isinstance(binding, CloudWorkspaceBinding):
            active_workspace_ids.add(binding.workspace_id)
    return active_workspace_ids


async def _cloud_workspace_gc_config() -> dict[str, Any]:
    cloud_workspace_config = dict(_load_cloud_workspace_config())
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
    for record in await session_manager.list_workspace_records():
        if record.provider_instance_id != provider_instance_id.strip():
            continue
        if not _workspace_record_is_gc_eligible(
            record,
            now=now,
            active_workspace_ids=active_workspace_ids,
        ):
            continue
        await session_manager.update_workspace_record_status(
            record.workspace_record_id,
            status="cleaning",
            cleanup_error=None,
        )
        try:
            _ = await asyncio.to_thread(
                cleanup_cloud_workspace_from_config,
                cloud_workspace_config,
                record.workspace_id,
                active_workspace_ids=active_workspace_ids,
            )
        except Exception as exc:
            await session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="cleanup_failed",
                cleanup_error=str(exc) or "workspace cleanup failed",
            )
            logger.exception(
                "Durable workspace GC failed workspace_id=%s",
                record.workspace_id,
            )
            continue
        await session_manager.update_workspace_record_status(
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
    return await asyncio.to_thread(
        cleanup_stale_cloud_workspaces_from_config,
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
        await asyncio.sleep(interval)


# Global session manager
session_manager = _build_session_manager()


def _key_error_detail(exc: KeyError) -> str:
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


def _owner_conflict_http_exception(
    exc: SessionOwnershipConflictError,
    *,
    session_id: str,
) -> HTTPException:
    if exc.reason == SessionOwnershipConflictReason.MISSING_OWNER:
        return HTTPException(
            status_code=404,
            detail=f"Session not found: {session_id}",
        )
    return HTTPException(status_code=409, detail=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    # Startup
    _log_development_mode_warning(_load_server_config())
    await _cleanup_cloud_workspaces_on_startup()
    try:
        await session_manager.backfill_owner_leases()
    except Exception:
        logger.exception("Failed to backfill owner leases during startup")
    cleanup_task = asyncio.create_task(_cleanup_idle_sessions())
    owner_renew_task = asyncio.create_task(_renew_owner_leases())
    cloud_workspace_gc_task = asyncio.create_task(
        _cleanup_stale_cloud_workspaces_periodically()
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
        for session_id in await session_manager.list_sessions_async():
            try:
                await session_manager.shutdown_session_runtime(session_id)
            except Exception:
                logger.warning(
                    "Failed to shut down runtime for session %s during server shutdown",
                    session_id,
                    exc_info=True,
                )
    finally:
        try:
            await session_manager.release_owned_sessions()
        finally:
            await session_manager.close()

    logger.info("HTTP server shut down")


app = FastAPI(title="Coding Agent HTTP API", lifespan=lifespan)

# Add rate limiter to app state
app.state.limiter = limiter

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Add exception handler for rate limit exceeded
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    raise HTTPException(status_code=429, detail=str(exc))


def _session_to_dict(session: Session) -> dict[str, Any]:
    """Convert session state to dictionary."""
    return session.as_dict()


def _session_owner_label(session: Session) -> str | None:
    origin = session.origin
    if origin is None:
        return None
    owner_label = origin.get("owner_label")
    return owner_label if isinstance(owner_label, str) and owner_label else None


def _auth_context_can_access_session(
    auth_context: AuthContext | None,
    session: Session,
) -> bool:
    if auth_context is None:
        return True
    if auth_context.scope == "admin":
        return True
    return _session_owner_label(session) == auth_context.owner_label


def _require_admin_context(auth_context: AuthContext | None) -> None:
    if auth_context is None:
        return
    if auth_context.scope != "admin":
        raise HTTPException(status_code=403, detail="Admin token required")


def _workspace_summary_response(
    entry: WorkspaceInventoryEntry,
) -> WorkspaceSummarySchema:
    return WorkspaceSummarySchema(
        workspace_id=entry.workspace_id,
        status=entry.status,
        updated_at=entry.updated_at,
    )


def _remote_retention_enabled() -> bool:
    return _load_remote_retention_config().get("enabled") is True


def _configured_provider_instance_id() -> str | None:
    provider_instance_id = _load_cloud_workspace_config().get("provider_instance_id")
    if isinstance(provider_instance_id, str) and provider_instance_id.strip():
        return provider_instance_id.strip()
    return None


def _workspace_record_summary_response(
    record: WorkspaceRecord,
) -> WorkspaceSummarySchema:
    updated_at = record.updated_at or record.created_at or datetime.now(UTC)
    local_provider_instance_id = _configured_provider_instance_id()
    return WorkspaceSummarySchema(
        workspace_id=record.workspace_id,
        status=record.status,
        updated_at=updated_at,
        session_id=record.session_id,
        provider=record.provider,
        provider_instance_id=record.provider_instance_id,
        workspace_host_label=record.workspace_host_label,
        source_kind=record.source_kind,
        retention_policy=record.retention_policy,
        expires_at=record.expires_at,
        cleanup_error=record.cleanup_error,
        is_local=(
            local_provider_instance_id is not None
            and record.provider_instance_id == local_provider_instance_id
        ),
    )


def _workspace_archive_manifest_response(
    manifest: WorkspaceArchiveManifest,
) -> WorkspaceArchiveManifestResponse:
    return WorkspaceArchiveManifestResponse(
        workspace_id=manifest.workspace_id,
        session_id=manifest.session_id,
        format=manifest.format,
        generated_at=manifest.generated_at,
        file_count=manifest.file_count,
        total_bytes=manifest.total_bytes,
        changed_files=manifest.changed_files,
        deleted_files=manifest.deleted_files,
        excluded_files=manifest.excluded_files,
        archive_sha256=manifest.archive_sha256,
    )


def _durable_workspace_retention_not_implemented() -> HTTPException:
    return HTTPException(
        status_code=501,
        detail="Durable remote workspace retention is not implemented yet.",
    )


def _execution_binding_from_request(
    body: CreateSessionRequest | None,
) -> ExecutionBinding | None:
    if body is None or body.execution_binding is None:
        return None
    binding = body.execution_binding
    if binding.kind == "local":
        return LocalExecutionBinding(workspace_root=binding.workspace_root)
    return CloudWorkspaceBinding(
        workspace_url=binding.workspace_url,
        workspace_id=binding.workspace_id,
    )


def _provisioned_execution_binding_from_request(
    body: CreateSessionRequest | None,
) -> ExecutionBinding | None:
    if body is None:
        return None
    if body.execution_binding is not None and body.workspace_source is not None:
        raise ValueError(
            "execution_binding and workspace_source cannot be set together"
        )
    explicit_binding = _execution_binding_from_request(body)
    if explicit_binding is not None:
        return explicit_binding
    if body.workspace_source is None:
        return None

    cloud_workspace_config = _load_cloud_workspace_config()
    if cloud_workspace_config.get("enabled") is not True:
        raise ValueError(
            "cloud workspace provisioning requires cloud_workspace.enabled=true"
        )
    _validate_workspace_source_phase_policy(
        body.workspace_source.model_dump(mode="python"),
        cloud_workspace_config,
    )
    return provision_cloud_binding_from_config(
        cloud_workspace_config,
        body.workspace_source.model_dump(mode="python"),
    )


def _validate_workspace_source_phase_policy(
    workspace_source: dict[str, object],
    cloud_workspace_config: dict[str, Any],
) -> None:
    setup_commands = workspace_source.get("setup_commands")
    if setup_commands is None:
        return
    remote_phases = cloud_workspace_config.get("remote_phases")
    setup_phase = (
        remote_phases.get("setup") if isinstance(remote_phases, dict) else None
    )
    allow_request_commands = (
        isinstance(setup_phase, dict)
        and setup_phase.get("allow_request_commands") is True
    )
    if not allow_request_commands:
        raise ValueError(
            "workspace_source.setup_commands requires "
            "remote_phases.setup.allow_request_commands=true"
        )
    raise ValueError("setup phase execution is not implemented yet")


def _session_origin_from_request(
    body: CreateSessionRequest | None,
    binding: ExecutionBinding | None,
    auth_context: AuthContext | None = None,
) -> dict[str, str]:
    origin = {
        "channel": "http",
        "binding_kind": "local" if binding is None else binding.kind,
    }
    if body is not None and body.workspace_source is not None:
        origin["workspace_source_kind"] = body.workspace_source.kind
    if auth_context is not None:
        origin["owner_label"] = auth_context.owner_label
        origin["auth_scope"] = auth_context.scope
    return origin


def _setup_phase_exception_detail(exc: BaseException) -> str | None:
    notes = [
        note
        for note in getattr(exc, "__notes__", ())
        if isinstance(note, str) and note.startswith("setup phase ")
    ]
    if not notes:
        return None

    returncode = getattr(exc, "returncode", None)
    if isinstance(returncode, int):
        prefix = f"setup phase failed with exit code {returncode}"
    else:
        prefix = "setup phase failed"
    return "\n".join([prefix, *notes])


def _http_exception_detail(exc: BaseException) -> str:
    setup_detail = _setup_phase_exception_detail(exc)
    if setup_detail is not None:
        return setup_detail
    return str(exc)


def _http_safe_tool_result_payload(msg: ToolResultDelta) -> dict[str, Any]:
    return {
        "session_id": msg.session_id,
        "agent_id": msg.agent_id,
        "tool_name": msg.tool_name,
        "call_id": msg.call_id,
        "result": None,
        "display_result": msg.display_result,
        "is_error": msg.is_error,
        "timestamp": msg.timestamp.isoformat(),
    }


def _wire_message_to_event(msg: WireMessage) -> dict[str, str]:
    """Convert wire message to SSE event."""
    match msg:
        case TurnEnd():
            return {
                "event": "TurnEnd",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "turn_id": msg.turn_id,
                        "completion_status": msg.completion_status,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case TurnBegin():
            return {
                "event": "TurnBegin",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case StreamDelta():
            return {
                "event": "StreamDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "content": msg.content,
                        "role": msg.role,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ThinkingDelta():
            return {
                "event": "ThinkingDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "text": msg.text,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case TurnStatusDelta():
            return {
                "event": "TurnStatusDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "phase": msg.phase,
                        "elapsed_seconds": msg.elapsed_seconds,
                        "tokens_in": msg.tokens_in,
                        "tokens_out": msg.tokens_out,
                        "model_name": msg.model_name,
                        "context_percent": msg.context_percent,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ToolCallDelta():
            return {
                "event": "ToolCallDelta",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "tool_name": msg.tool_name,
                        "arguments": msg.arguments,
                        "call_id": msg.call_id,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ToolResultDelta():
            return {
                "event": "ToolResultDelta",
                "data": json.dumps(_http_safe_tool_result_payload(msg)),
            }
        case ToolCallBegin():
            return {
                "event": "ToolCallBegin",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "call_id": msg.call_id,
                        "tool": msg.tool,
                        "args": msg.args,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ToolCallEnd():
            return {
                "event": "ToolCallEnd",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "call_id": msg.call_id,
                        "result": msg.result,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ApprovalRequest():
            return {
                "event": "ApprovalRequest",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "request_id": msg.request_id,
                        "tool_call": {
                            "tool_name": msg.tool_call.tool_name
                            if msg.tool_call
                            else "",
                            "arguments": msg.tool_call.arguments
                            if msg.tool_call
                            else {},
                            "call_id": msg.tool_call.call_id if msg.tool_call else "",
                        },
                        "timeout_seconds": msg.timeout_seconds,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ApprovalResponse():
            return {
                "event": "ApprovalResponse",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "request_id": msg.request_id,
                        "approved": msg.approved,
                        "feedback": msg.feedback,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case ErrorMessage():
            return {
                "event": "ErrorMessage",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "content": msg.content,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case StepInfo():
            return {
                "event": "StepInfo",
                "data": json.dumps(
                    {
                        "session_id": msg.session_id,
                        "agent_id": msg.agent_id,
                        "step_number": msg.step_number,
                        "max_steps": msg.max_steps,
                        "timestamp": msg.timestamp.isoformat(),
                    }
                ),
            }
        case _:
            return {
                "event": "Unknown",
                "data": json.dumps(
                    {
                        "type": type(msg).__name__,
                        "session_id": getattr(msg, "session_id", None),
                        "agent_id": getattr(msg, "agent_id", None),
                    }
                ),
            }


async def _broadcast_event(session: Session, event: dict[str, str]) -> None:
    """Broadcast event to all connected clients."""
    active_queues: list[asyncio.Queue[dict[str, str]]] = []
    full_pruned_count = 0
    failed_pruned_count = 0

    for queue in session.event_queues:
        try:
            queue.put_nowait(event)
            active_queues.append(queue)
        except asyncio.QueueFull:
            full_pruned_count += 1
        except Exception:
            failed_pruned_count += 1
            logger.debug("Dropping closed event queue", exc_info=True)

    session.event_queues = active_queues

    if full_pruned_count:
        logger.info(
            "Pruned %d full event queue(s) for session %s",
            full_pruned_count,
            session.id,
        )
    if failed_pruned_count:
        logger.info(
            "Pruned %d failed event queue(s) for session %s",
            failed_pruned_count,
            session.id,
        )


async def _cleanup_event_queue_on_disconnect(
    session_id: str,
    queue: asyncio.Queue[dict[str, str]],
) -> None:
    try:
        await asyncio.shield(
            session_manager.remove_event_queue_async(session_id, queue)
        )
    except KeyError:
        logger.debug(
            "Event queue cleanup skipped for already-removed session %s",
            session_id,
            exc_info=True,
        )


async def _cleanup_idle_sessions() -> None:
    """Background task to clean up idle sessions."""
    while True:
        await asyncio.sleep(60)  # Check every minute
        try:
            await session_manager.cleanup_idle_sessions(SESSION_IDLE_TIMEOUT_MINUTES)
        except Exception:
            logger.exception("Error during idle session cleanup")


async def stream_wire_messages(
    wire: LocalWire,
    task: asyncio.Task[Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Stream wire messages as SSE events.

    Consumes messages from the wire's outgoing queue and yields SSE events.
    Stops when a TurnEnd message is received.
    """
    while True:
        get_message_task = asyncio.create_task(wire.get_next_outgoing())
        try:
            if task is not None:
                done, pending = await asyncio.wait(
                    {get_message_task, task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done and get_message_task in pending:
                    get_message_task.cancel()
                    try:
                        await get_message_task
                    except asyncio.CancelledError:
                        pass
                    task.result()
                    break
                msg = get_message_task.result()
            else:
                msg = await get_message_task
            event = _wire_message_to_event(msg)
            yield event

            if isinstance(msg, TurnEnd) and not msg.agent_id:
                break
        except asyncio.CancelledError:
            if not get_message_task.done():
                get_message_task.cancel()
            # Client disconnected
            raise
        except Exception as e:
            if not get_message_task.done():
                get_message_task.cancel()
            logger.exception("Error streaming wire message")
            yield {
                "event": "Error",
                "data": json.dumps({"error": str(e)}),
            }
            break


@app.get("/healthz", response_model=HealthResponse)
@limiter.limit(RateLimits.HEALTH)
async def liveness_check(request: Request) -> HealthResponse:
    return HealthResponse(
        status="healthy",
        sessions=await session_manager.count_sessions_async(),
        version="2.0.0",
    )


@app.get("/readyz", response_model=ReadinessResponse)
@limiter.limit(RateLimits.HEALTH)
async def readiness_check(request: Request, response: Response) -> ReadinessResponse:
    try:
        session_store_ok = bool(await session_manager.check_health_async())
    except Exception:
        logger.exception("Session store readiness check failed")
        session_store_ok = False

    try:
        rate_limiter_ok = bool(limiter._storage.check())
    except Exception:
        logger.exception("Rate limiter readiness check failed")
        rate_limiter_ok = False

    checks = {
        "session_store": "ok" if session_store_ok else "error",
        "rate_limiter": "ok" if rate_limiter_ok else "error",
    }
    ready = session_store_ok and rate_limiter_ok

    try:
        cloud_workspace_config = _load_cloud_workspace_config()
        if cloud_workspace_config.get("enabled") is True:
            cloud_workspace_ok = bool(
                await asyncio.to_thread(
                    cloud_workspace_ready_from_config,
                    cloud_workspace_config,
                )
            )
            checks["cloud_workspace"] = "ok" if cloud_workspace_ok else "error"
            ready = ready and cloud_workspace_ok
    except Exception:
        logger.exception("Cloud workspace readiness check failed")
        checks["cloud_workspace"] = "error"
        ready = False

    if not ready:
        response.status_code = 503
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        checks=checks,
    )


@app.post("/sessions", response_model=SessionResponse)
@limiter.limit(RateLimits.CREATE_SESSION)
async def create_session(
    request: Request,
    body: CreateSessionRequest | None = None,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionResponse:
    """Create new session with AgentLoop integration."""
    # Use defaults if no body provided
    repo_path = None if body is None or body.repo_path is None else Path(body.repo_path)
    approval_policy_str = body.approval_policy if body else "auto"
    agent_defaults = _load_agent_runtime_defaults()

    # Map string to ApprovalPolicy enum
    approval_policy_map = {
        "yolo": ApprovalPolicy.YOLO,
        "interactive": ApprovalPolicy.INTERACTIVE,
        "auto": ApprovalPolicy.AUTO,
    }
    approval_policy = approval_policy_map.get(approval_policy_str, ApprovalPolicy.AUTO)
    provisioned_binding: CloudWorkspaceBinding | None = None

    try:
        execution_binding = _provisioned_execution_binding_from_request(body)
        if (
            body is not None
            and body.workspace_source is not None
            and isinstance(execution_binding, CloudWorkspaceBinding)
        ):
            provisioned_binding = execution_binding
        session_id = await session_manager.create_session(
            repo_path=repo_path,
            origin=_session_origin_from_request(
                body,
                execution_binding,
                auth_context,
            ),
            execution_binding=execution_binding,
            approval_policy=approval_policy,
            provider_name=(
                body.provider
                if body and body.provider is not None
                else _optional_config_string(agent_defaults.get("provider"))
            ),
            model_name=(
                body.model
                if body and body.model is not None
                else _optional_config_string(agent_defaults.get("model"))
            ),
            base_url=body.base_url if body else None,
            max_steps=(
                body.max_steps
                if body and body.max_steps is not None
                else _config_int_or_default(agent_defaults.get("max_steps"), 30)
            ),
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        if provisioned_binding is not None:
            try:
                await asyncio.to_thread(
                    _cleanup_provisioned_cloud_binding, provisioned_binding
                )
            except Exception:
                logger.exception("Failed to roll back provisioned cloud workspace")
        raise
    except Exception as exc:
        if provisioned_binding is not None:
            try:
                await asyncio.to_thread(
                    _cleanup_provisioned_cloud_binding, provisioned_binding
                )
            except Exception:
                logger.exception("Failed to roll back provisioned cloud workspace")
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, RuntimeError):
            raise HTTPException(
                status_code=500, detail=_http_exception_detail(exc)
            ) from exc
        if isinstance(exc, (ValueError, TypeError)):
            raise HTTPException(
                status_code=400, detail=_http_exception_detail(exc)
            ) from exc
        raise HTTPException(
            status_code=500, detail=_http_exception_detail(exc)
        ) from exc

    logger.info(f"Created session: {session_id}")
    return SessionResponse(session_id=session_id)


@app.post("/sessions/{session_id}/prompt")
@limiter.limit(RateLimits.SEND_PROMPT)
async def send_prompt(
    request: Request,
    session_id: str,
    body: PromptRequest | None = None,
    prompt: str | None = None,  # Backward compat: query param
    api_key: str | None = Depends(verify_api_key),
) -> EventSourceResponse:
    """Send message, returns SSE stream.

    Returns 409 if a turn is already in progress.
    Accepts prompt via JSON body (preferred) or query param (backward compat).
    """
    # Get prompt from body or query param (body takes precedence)
    prompt_text = body.prompt if body else prompt
    if not prompt_text:
        raise HTTPException(status_code=422, detail="Prompt is required")

    try:
        session = await session_manager.prepare_session_turn(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(
                status_code=409, detail="Turn already in progress"
            ) from exc
        raise

    session.turn_in_progress = True
    session.last_activity = datetime.now()

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        """Generate SSE events for the turn."""
        try:
            session.task = asyncio.create_task(
                session_manager.run_agent(session_id, prompt_text)
            )

            async for event in stream_wire_messages(session.wire, session.task):
                await _broadcast_event(session, event)
                yield event

        except Exception as e:
            logger.exception("Error during turn")
            error_data = {
                "event": "Error",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat(),
                    }
                ),
            }
            await _broadcast_event(session, error_data)
            yield error_data
        finally:
            if session.task is not None:
                try:
                    await session.task
                except Exception:
                    pass
                session.task = None
            session.turn_in_progress = False
            session.last_activity = datetime.now()

    # Return SSE stream from wire
    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@app.post("/sessions/{session_id}/approve", response_model=ApprovalResponseSchema)
@limiter.limit(RateLimits.APPROVE)
async def approve_request(
    request: Request,
    session_id: str,
    body: ApproveRequest | None = None,
    request_id: str | None = None,  # Backward compat: query param
    approved: bool | None = None,  # Backward compat: query param
    feedback: str | None = None,  # Backward compat: query param
    scope: str | None = None,  # Backward compat: query param
    api_key: str | None = Depends(verify_api_key),
) -> ApprovalResponseSchema:
    """Respond to approval request.

    Accepts parameters via JSON body (preferred) or query params (backward compat).
    """
    # Get values from body or query params (body takes precedence)
    req_id = body.request_id if body else request_id
    is_approved = body.approved if body else approved
    fb = body.feedback if body else feedback
    resolved_scope = cast(
        Literal["once", "session"],
        body.scope if body else (scope or "once"),
    )

    if resolved_scope not in {"once", "session"}:
        raise HTTPException(status_code=422, detail="scope must be 'once' or 'session'")

    if req_id is None:
        raise HTTPException(status_code=422, detail="request_id is required")
    if is_approved is None:
        raise HTTPException(status_code=422, detail="approved is required")

    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        approval_response = await session_manager.submit_approval_response(
            session_id=session_id,
            request_id=req_id,
            approved=is_approved,
            feedback=fb,
            scope=resolved_scope,
        )
        if approval_response is None:
            raise HTTPException(status_code=400, detail="No pending approval request")
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Unexpected error while submitting approval for session %s",
            session_id,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return ApprovalResponseSchema(
        status="ok",
        request_id=req_id,
        decision="approved" if approval_response.approved else "denied",
    )


@app.get("/sessions/{session_id}/events")
@limiter.limit(RateLimits.EVENTS)
async def get_events(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> EventSourceResponse:
    """Persistent SSE event stream (fan-out supported)."""
    try:
        await session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc

    try:
        await session_manager.authorize_event_stream(session_id)
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc

    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
    try:
        await session_manager.register_owned_event_queue_async(session_id, queue)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        """Generate events from queue."""
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    try:
                        await session_manager.verify_event_stream_ownership(session_id)
                    except SessionOwnershipConflictError:
                        break
                    yield event
                    if event.get("event") == "SessionClosed":
                        break
                except asyncio.TimeoutError:
                    if not await session_manager.has_session_async(session_id):
                        break
                    try:
                        await session_manager.verify_event_stream_ownership(session_id)
                    except SessionOwnershipConflictError:
                        break
                    try:
                        if not await session_manager.has_event_queue_async(
                            session_id, queue
                        ):
                            break
                    except KeyError:
                        break
                    # Send keepalive
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            # Client disconnected
            raise
        finally:
            await _cleanup_event_queue_on_disconnect(session_id, queue)

    return EventSourceResponse(event_generator())


@app.post("/sessions/{session_id}/cancel", response_model=CancelSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def cancel_session_turn(
    request: Request,
    response: Response,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> CancelSessionResponse:
    del request
    try:
        session = await session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    if not _auth_context_can_access_session(auth_context, session):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        result = await session_manager.cancel_session_turn(session_id)
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if result.status == "cancelling":
        response.status_code = 202
        await _broadcast_event(
            session,
            {
                "event": "TurnCancelling",
                "data": json.dumps(
                    {"session_id": session_id, "turn_id": result.turn_id}
                ),
            },
        )

    return CancelSessionResponse(
        session_id=result.session_id,
        turn_id=result.turn_id,
        status=result.status,
    )


@app.get("/workspaces", response_model=WorkspaceListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_workspaces(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceListResponse:
    del request
    _require_admin_context(auth_context)
    if _remote_retention_enabled():
        records = await session_manager.list_workspace_records()
        return WorkspaceListResponse(
            workspaces=[
                _workspace_record_summary_response(record) for record in records
            ]
        )
    config = _load_cloud_workspace_config()
    entries = await asyncio.to_thread(
        list_cloud_workspaces_from_config,
        config,
        active_workspace_ids=await _active_cloud_workspace_ids(),
    )
    return WorkspaceListResponse(
        workspaces=[_workspace_summary_response(entry) for entry in entries]
    )


@app.post("/workspaces/gc", response_model=WorkspaceGcResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def run_workspace_gc(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceGcResponse:
    del request
    _require_admin_context(auth_context)
    cleaned_count = await _cleanup_cloud_workspaces_from_config(
        await _cloud_workspace_gc_config()
    )
    return WorkspaceGcResponse(cleaned_count=cleaned_count)


@app.get("/workspaces/{workspace_id}", response_model=WorkspaceSummarySchema)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceSummarySchema:
    del request
    _require_admin_context(auth_context)
    if _remote_retention_enabled():
        record = await session_manager.load_workspace_record_by_workspace_id(
            workspace_id
        )
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"Workspace not found: {workspace_id}"
            )
        return _workspace_record_summary_response(record)
    try:
        entry = await asyncio.to_thread(
            get_cloud_workspace_from_config,
            _load_cloud_workspace_config(),
            workspace_id,
            active_workspace_ids=await _active_cloud_workspace_ids(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _workspace_summary_response(entry)


@app.post(
    "/workspaces/{workspace_id}/retain", response_model=WorkspaceRetentionResponse
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def retain_workspace(
    request: Request,
    workspace_id: str,
    body: WorkspaceRetentionRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request, workspace_id, body
    _require_admin_context(auth_context)
    raise _durable_workspace_retention_not_implemented()


@app.post("/workspaces/{workspace_id}/pin", response_model=WorkspaceRetentionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def pin_workspace(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request, workspace_id
    _require_admin_context(auth_context)
    raise _durable_workspace_retention_not_implemented()


@app.post("/workspaces/{workspace_id}/unpin", response_model=WorkspaceRetentionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def unpin_workspace(
    request: Request,
    workspace_id: str,
    body: WorkspaceUnpinRequest | None = None,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request, workspace_id, body
    _require_admin_context(auth_context)
    raise _durable_workspace_retention_not_implemented()


@app.delete("/workspaces/{workspace_id}", response_model=WorkspaceCleanupResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def cleanup_workspace(
    request: Request,
    response: Response,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceCleanupResponse:
    del request
    _require_admin_context(auth_context)
    try:
        entry = await asyncio.to_thread(
            cleanup_cloud_workspace_from_config,
            _load_cloud_workspace_config(),
            workspace_id,
            active_workspace_ids=await _active_cloud_workspace_ids(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Cloud workspace cleanup failed workspace_id=%s", workspace_id)
        response.status_code = 500
        return WorkspaceCleanupResponse(
            workspace_id=workspace_id,
            status="cleanup_failed",
            error=str(exc),
        )
    return WorkspaceCleanupResponse(workspace_id=entry.workspace_id, status="cleaned")


@app.get(
    "/workspaces/{workspace_id}/archive/manifest",
    response_model=WorkspaceArchiveManifestResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace_archive_manifest_by_id(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceArchiveManifestResponse:
    del request
    _require_admin_context(auth_context)
    try:
        manifest = await asyncio.to_thread(
            workspace_archive_manifest_from_config,
            _load_cloud_workspace_config(),
            workspace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _workspace_archive_manifest_response(manifest)


@app.get(
    "/workspaces/{workspace_id}/archive",
    response_model=WorkspaceArchiveResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace_archive_by_id(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceArchiveResponse:
    del request
    _require_admin_context(auth_context)
    try:
        archive_base64 = await asyncio.to_thread(
            export_workspace_archive_by_id_from_config,
            _load_cloud_workspace_config(),
            workspace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceArchiveResponse(format="tar.gz", archive_base64=archive_base64)


@app.get("/sessions", response_model=SessionListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_sessions(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionListResponse:
    del request
    summaries: list[SessionSummaryResponse] = []
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        summaries.append(SessionSummaryResponse(**session.as_dict()))
    return SessionListResponse(sessions=summaries)


@app.get("/sessions/{session_id}", response_model=SessionSummaryResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionSummaryResponse:
    """Get session state."""
    del request
    try:
        session = await session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    if not _auth_context_can_access_session(auth_context, session):
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionSummaryResponse(**session.as_dict())


async def _get_visible_session(
    session_id: str,
    auth_context: AuthContext | None,
) -> Session:
    try:
        session = await session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc

    if not _auth_context_can_access_session(auth_context, session):
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _compact_session_result_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _session_result_tool_detail(call: ToolCallRecord) -> str | None:
    for key in _SESSION_RESULT_TOOL_DETAIL_KEYS:
        value = call.arguments.get(key)
        if not isinstance(value, str) or value.strip() == "":
            continue
        return _compact_session_result_text(
            value,
            max_chars=_SESSION_RESULT_MAX_TOOL_DETAIL_CHARS,
        )
    return None


def _session_result_verification_summary(turn: TurnTrace | None) -> str | None:
    if turn is None or not turn.tool_calls:
        return None

    items: list[str] = []
    for call in turn.tool_calls[:_SESSION_RESULT_MAX_TOOL_ITEMS]:
        name = call.name.strip() or "unnamed_tool"
        detail = _session_result_tool_detail(call)
        if detail is None:
            items.append(name)
        else:
            items.append(f"{name}: {detail}")

    remaining = len(turn.tool_calls) - len(items)
    if remaining > 0:
        items.append(f"+{remaining} more")
    return "Tool activity: " + "; ".join(items)


def _session_runtime_tape(session: Session) -> Tape | None:
    runtime_ctx = session.runtime_ctx
    if runtime_ctx is None:
        return None
    tape = getattr(runtime_ctx, "tape", None)
    if tape is None:
        return None
    if not isinstance(tape, Tape):
        raise TypeError("session runtime context has invalid tape")
    return tape


async def _session_result_tape(session: Session) -> Tape | None:
    runtime_tape = _session_runtime_tape(session)
    if runtime_tape is not None:
        return runtime_tape
    return await session_manager._restore_tape(session.tape_id)


async def _session_result_latest_turn(session: Session) -> TurnTrace | None:
    tape = await _session_result_tape(session)
    if tape is None:
        return None
    turns = extract_turns(tape.snapshot())
    if not turns:
        return None
    return turns[-1]


def _session_result_failure_details(session: Session) -> str | None:
    details = session.last_failure_details
    if details is not None:
        return details
    if session.turn_status == "failed":
        return "Session turn failed; no failure details were recorded."
    return None


@app.get("/sessions/{session_id}/result", response_model=SessionResultResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_result(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> SessionResultResponse:
    del request
    session = await _get_visible_session(session_id, auth_context)
    summary = session.as_dict()
    latest_turn = await _session_result_latest_turn(session)
    return SessionResultResponse(
        session_id=session.id,
        status=summary["status"],
        turn_status=summary["turn_status"],
        turn_id=session.current_turn_id,
        workspace_id=summary["workspace_id"],
        origin=session.origin,
        provider_name=session.provider_name,
        model_name=session.model_name,
        final_answer=None if latest_turn is None else latest_turn.final_output,
        verification_summary=_session_result_verification_summary(latest_turn),
        failure_details=_session_result_failure_details(session),
    )


@app.get(
    "/sessions/{session_id}/workspace/diff",
    response_model=WorkspaceDiffResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_diff(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceDiffResponse:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    try:
        diff = await session_manager.export_workspace_archive(
            session_id,
            lambda binding: workspace_diff_from_config(
                _load_cloud_workspace_config(),
                binding.workspace_id,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

    return WorkspaceDiffResponse(
        session_id=session_id,
        workspace_id=diff.workspace_id,
        files=[
            WorkspaceDiffFileSchema(
                path=file.path,
                status=file.status,
                old_path=file.old_path,
                additions=file.additions,
                deletions=file.deletions,
                binary=file.binary,
            )
            for file in diff.files
        ],
        additions=diff.additions,
        deletions=diff.deletions,
    )


@app.get(
    "/sessions/{session_id}/workspace/patch",
    response_model=WorkspacePatchResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_patch(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspacePatchResponse:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    try:
        patch = await session_manager.export_workspace_archive(
            session_id,
            lambda binding: workspace_patch_from_config(
                _load_cloud_workspace_config(),
                binding.workspace_id,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

    return WorkspacePatchResponse(
        session_id=session_id,
        workspace_id=patch.workspace_id,
        format=patch.format,
        patch=patch.patch,
    )


@app.post("/sessions/{session_id}/publish", response_model=PublishSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def publish_session_result(
    request: Request,
    session_id: str,
    body: PublishSessionRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> PublishSessionResponse:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    branch_name = body.branch_name or f"coding-agent/session-{session_id}"
    commit_message = f"Apply coding-agent remote session {session_id} changes"
    publication_config = _load_remote_publication_config()
    try:
        publication = await session_manager.export_workspace_archive(
            session_id,
            lambda binding: publish_workspace_branch_from_config(
                _load_cloud_workspace_config(),
                publication_config,
                binding.workspace_id,
                branch_name,
                commit_message,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

    if body.mode == "pr":
        return await _publish_session_pr_response(
            session_id,
            publication,
            publication_config,
        )

    return PublishSessionResponse(
        session_id=session_id,
        mode="branch",
        status="published",
        branch_name=publication.branch_name,
        pushed_ref=publication.pushed_ref,
        commit_sha=publication.commit_sha,
        remote_url=publication.remote_url,
        pr_url=None,
        error=None,
    )


async def _publish_session_pr_response(
    session_id: str,
    publication: WorkspaceBranchPublication,
    publication_config: dict[str, Any],
) -> PublishSessionResponse:
    try:
        pr_url = await _create_github_pull_request(
            session_id,
            publication,
            publication_config,
        )
    except GitHubPrUnsupportedError as exc:
        return PublishSessionResponse(
            session_id=session_id,
            mode="pr",
            status="unsupported",
            branch_name=publication.branch_name,
            pushed_ref=publication.pushed_ref,
            commit_sha=publication.commit_sha,
            remote_url=publication.remote_url,
            pr_url=None,
            error=str(exc),
        )
    except GitHubPrPublicationError as exc:
        return PublishSessionResponse(
            session_id=session_id,
            mode="pr",
            status="failed",
            branch_name=publication.branch_name,
            pushed_ref=publication.pushed_ref,
            commit_sha=publication.commit_sha,
            remote_url=publication.remote_url,
            pr_url=None,
            error=str(exc),
        )
    return PublishSessionResponse(
        session_id=session_id,
        mode="pr",
        status="published",
        branch_name=publication.branch_name,
        pushed_ref=publication.pushed_ref,
        commit_sha=publication.commit_sha,
        remote_url=publication.remote_url,
        pr_url=pr_url,
        error=None,
    )


async def _create_github_pull_request(
    session_id: str,
    publication: WorkspaceBranchPublication,
    publication_config: dict[str, Any],
) -> str:
    github_config = publication_config.get("github")
    if not isinstance(github_config, dict) or github_config.get("enabled") is not True:
        raise GitHubPrUnsupportedError(
            "remote_publication.github.enabled=true is required for GitHub PR "
            "publication; branch was published and can be opened manually"
        )
    github_config = cast(dict[str, object], github_config)
    token_env = github_config.get("token_env")
    if not isinstance(token_env, str) or not token_env.strip():
        raise GitHubPrUnsupportedError(
            "remote_publication.github.token_env is required for GitHub PR "
            "publication; branch was published and can be opened manually"
        )
    token = os.environ.get(token_env.strip())
    if token is None or not token.strip():
        raise GitHubPrUnsupportedError(
            f"remote_publication.github.token_env is not set: {token_env.strip()}; "
            "branch was published and can be opened manually"
        )
    base_branch = github_config.get("base_branch")
    if not isinstance(base_branch, str) or not base_branch.strip():
        raise GitHubPrUnsupportedError(
            "remote_publication.github.base_branch is required for GitHub PR "
            "publication; branch was published and can be opened manually"
        )
    owner, repo = _github_repo_from_remote_url(publication.remote_url)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{_GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token.strip()}",
                    "X-GitHub-Api-Version": _GITHUB_API_VERSION,
                },
                json={
                    "title": f"Coding agent remote session {session_id}",
                    "head": publication.branch_name,
                    "base": base_branch.strip(),
                    "body": (
                        f"Remote coding-agent session `{session_id}` published "
                        f"commit `{publication.commit_sha}`."
                    ),
                },
            )
            response.raise_for_status()
    except Exception as exc:
        raise GitHubPrPublicationError(f"GitHub PR publication failed: {exc}") from exc
    payload = cast(object, response.json())
    if not isinstance(payload, dict):
        raise GitHubPrPublicationError(
            "GitHub PR publication failed: response must be a JSON object"
        )
    pr_url = payload.get("html_url")
    if not isinstance(pr_url, str) or not pr_url.strip():
        raise GitHubPrPublicationError(
            "GitHub PR publication failed: response missing html_url"
        )
    return pr_url


def _github_repo_from_remote_url(remote_url: str) -> tuple[str, str]:
    scp_match = _GITHUB_SCP_REMOTE_RE.fullmatch(remote_url.strip())
    if scp_match is not None:
        return _github_owner_repo_from_path(scp_match.group("path"))

    parsed = urlsplit(remote_url)
    if parsed.hostname != "github.com":
        raise GitHubPrUnsupportedError(
            "GitHub PR publication requires a github.com remote; branch was "
            "published and can be opened manually"
        )
    return _github_owner_repo_from_path(parsed.path)


def _github_owner_repo_from_path(path: str) -> tuple[str, str]:
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GitHubPrUnsupportedError(
            "GitHub PR publication could not derive owner/repo from remote URL; "
            "branch was published and can be opened manually"
        )
    return parts[0], parts[1]


async def _export_session_workspace_archive(
    session_id: str,
) -> WorkspaceArchiveResponse:
    try:
        archive_base64 = await session_manager.export_workspace_archive(
            session_id,
            lambda binding: export_workspace_archive_from_config(
                _load_cloud_workspace_config(),
                binding,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        logger.exception(
            "Cloud workspace archive download/export failed session_id=%s",
            session_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.exception(
            "Cloud workspace archive download/export failed session_id=%s",
            session_id,
        )
        raise

    return WorkspaceArchiveResponse(format="tar.gz", archive_base64=archive_base64)


@app.get(
    "/sessions/{session_id}/workspace/archive/manifest",
    response_model=WorkspaceArchiveManifestResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_archive_manifest(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> WorkspaceArchiveManifestResponse:
    del request, api_key
    try:
        manifest = await session_manager.export_workspace_archive(
            session_id,
            lambda binding: workspace_archive_manifest_from_config(
                _load_cloud_workspace_config(),
                binding.workspace_id,
                session_id=session_id,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return _workspace_archive_manifest_response(manifest)


@app.get(
    "/sessions/{session_id}/workspace/archive",
    response_model=WorkspaceArchiveResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_session_workspace_archive(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> WorkspaceArchiveResponse:
    del request, api_key
    return await _export_session_workspace_archive(session_id)


@app.get(
    "/sessions/{session_id}/workspace",
    response_model=WorkspaceArchiveResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_workspace_archive(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> WorkspaceArchiveResponse:
    del request, api_key
    return await _export_session_workspace_archive(session_id)


@app.post(
    "/sessions/{session_id}/checkpoints",
    response_model=CheckpointMetadataResponse,
)
@limiter.limit(RateLimits.CAPTURE_CHECKPOINT)
async def capture_checkpoint(
    request: Request,
    session_id: str,
    body: CheckpointCaptureRequest | None = None,
    api_key: str | None = Depends(verify_api_key),
) -> CheckpointMetadataResponse:
    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        checkpoint = await session_manager.capture_checkpoint(
            session_id,
            label=body.label if body else None,
            extra=None,
        )
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CheckpointMetadataResponse(
        checkpoint_id=checkpoint.checkpoint_id,
        tape_id=checkpoint.tape_id,
        session_id=checkpoint.session_id,
        entry_count=checkpoint.entry_count,
        window_start=checkpoint.window_start,
        created_at=checkpoint.created_at,
        label=checkpoint.label,
    )


@app.get("/sessions/{session_id}/checkpoints", response_model=CheckpointListResponse)
@limiter.limit(RateLimits.LIST_CHECKPOINTS)
async def list_checkpoints(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CheckpointListResponse:
    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    checkpoints = await session_manager.list_checkpoints(session_id)
    return CheckpointListResponse(
        checkpoints=[
            CheckpointMetadataResponse(
                checkpoint_id=checkpoint.checkpoint_id,
                tape_id=checkpoint.tape_id,
                session_id=checkpoint.session_id,
                entry_count=checkpoint.entry_count,
                window_start=checkpoint.window_start,
                created_at=checkpoint.created_at,
                label=checkpoint.label,
            )
            for checkpoint in checkpoints
        ]
    )


@app.post(
    "/sessions/{session_id}/checkpoints/{checkpoint_id}/restore",
    response_model=CheckpointRestoreResponse,
)
@limiter.limit(RateLimits.RESTORE_CHECKPOINT)
async def restore_checkpoint(
    request: Request,
    session_id: str,
    checkpoint_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CheckpointRestoreResponse:
    if not await session_manager.has_session_async(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        await session_manager.restore_checkpoint(session_id, checkpoint_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CheckpointRestoreResponse(
        status="restored",
        session_id=session_id,
        checkpoint_id=checkpoint_id,
    )


@app.delete("/sessions/{session_id}", response_model=CloseSessionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def close_session(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CloseSessionResponse:
    """Close session and release resources."""
    try:
        session = await session_manager.get_session_async(session_id)
        await session_manager.close_session(session_id)
    except SessionOwnershipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error while closing session %s", session_id)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    await _broadcast_event(
        session,
        {"event": "SessionClosed", "data": json.dumps({"session_id": session_id})},
    )

    logger.info(f"Closed session: {session_id}")
    return CloseSessionResponse(status="closed", session_id=session_id)


async def wait_for_approval(
    session_id: str,
    approval_req: ApprovalRequest,
) -> ApprovalResponse:
    """Wait for approval response from HTTP clients.

    This function is called by the agent loop when it needs approval.
    It will block until the user responds via the /approve endpoint
    or the timeout expires.
    """
    if not await session_manager.has_session_async(session_id):
        return ApprovalResponse(
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Session not found",
        )

    session = await session_manager.get_session_async(session_id)
    event = _wire_message_to_event(approval_req)
    await _broadcast_event(session, event)
    response = await session_manager.wait_for_http_approval(
        session_id=session_id,
        approval_req=approval_req,
        timeout_seconds=APPROVAL_TIMEOUT_SECONDS,
    )
    if not response.approved and response.feedback == "Approval timeout or error":
        timeout_event = {
            "event": "ApprovalTimeout",
            "data": json.dumps({"request_id": approval_req.request_id}),
        }
        await _broadcast_event(session, timeout_event)
    return response
