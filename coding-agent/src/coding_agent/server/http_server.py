"""FastAPI-based HTTP server for Coding Agent with REST endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from slowapi.errors import RateLimitExceeded
from sse_starlette.sse import EventSourceResponse

from agentkit.config.loader import load_config as load_agent_toml
from agentkit.errors import ConfigError
from agentkit.result.models import ArtifactRef, TurnResult
from agentkit.result.reducers import result_from_turn_trace
from agentkit.tape.extract import ToolCallRecord, TurnTrace, extract_turns
from agentkit.tape.tape import Tape
from coding_agent.approval import ApprovalPolicy
from coding_agent.bee_launch import BeeLaunchRecord, PGBeeLaunchStore
from coding_agent.bee_template_pack import (
    BeePackRegistry,
    BeeTemplatePackSource,
    build_bee_pack_dry_run_plan,
    validate_bee_pack_compatibility,
)
from coding_agent.bee_workspace import (
    BeeWorkspaceCommandIntent,
    BeeWorkspaceRunArtifactRecord,
    BeeWorkspaceTemplate,
    discover_bee_workspace_run_artifacts,
    discover_bee_workspace_templates,
    load_bee_workspace_command_intents,
)
from coding_agent.environment import (
    WorkspaceArchiveManifest,
    WorkspaceBranchPublication,
    WorkspaceDiff,
    WorkspaceDiffFile,
    WorkspaceInventoryEntry,
    WorkspacePatch,
    WorkspaceProviderCapabilities,
    cleanup_cloud_binding_from_config,
    cleanup_cloud_workspace_from_config,
    cleanup_stale_cloud_workspaces_from_config,
    cloud_client_factory_from_config,
    cloud_workspace_ready_from_config,
    export_workspace_archive_by_id_from_config,
    export_workspace_archive_from_config,
    get_cloud_workspace_from_config,
    list_cloud_workspaces_from_config,
    provision_cloud_binding_from_config,
    publish_workspace_branch_from_config,
    workspace_archive_manifest_from_config,
    workspace_diff_from_config,
    workspace_patch_from_config,
    workspace_provider_capabilities_from_config,
)
from coding_agent.external_executor import ExecutorRunRecord, PGExecutorRunStore
from coding_agent.observability import (
    prometheus_metrics_text,
    record_bee_pack_dry_run_metric,
    record_bee_pack_template_metric,
    record_bee_pack_validation_metric,
    record_http_request_metric,
)
from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    JSONObject,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.local_storage import local_sqlite_path_from_storage_config
from coding_agent.local_storage import normalize_storage_path
from coding_agent.events import DisplayEvent, DisplayEventStreamProjector
from coding_agent.scheduled_runs import (
    PGScheduledRunStore,
    ProactiveSignalRecord,
    ScheduleRecord,
    ScheduleTriggerRecord,
)
from coding_agent.topic_store import (
    PGTopicStore,
    TopicAnchorRecord,
    TopicCostRecord,
    TopicRecallLinkRecord,
    TopicRecord,
)
from coding_agent.server.auth import (
    AuthContext,
    auth_context_from_headers,
    verify_api_key,
)
from coding_agent.server.developer_console import (
    ConsoleActionSummary,
    ConsoleActionValidationSummary,
    ConsoleBeeCommandIntentSummary,
    ConsoleBeeLaunchSummary,
    ConsoleBeeNodeSummary,
    ConsoleBeePackCompatibilitySummary,
    ConsoleBeePackDryRunSummary,
    ConsoleBeePackSummary,
    ConsoleBeePackTemplateSummary,
    ConsoleBeePage,
    ConsoleBeeRunArtifactSummary,
    ConsoleBeeTaskSummary,
    ConsoleBeeTemplateSummary,
    ConsoleContextEvidence,
    ConsoleContextSectionSummary,
    ConsoleContextSummary,
    ConsoleCorrelationSummary,
    ConsoleDisplayEventSummary,
    ConsoleExecutorRunSummary,
    ConsoleInteractionSummary,
    ConsoleMemoryEvidence,
    ConsoleMemoryReviewSummary,
    ConsoleMemorySummary,
    ConsoleObservabilitySummary,
    ConsoleProactiveSignalSummary,
    ConsoleReleaseGateSummary,
    ConsoleReleaseSummary,
    ConsoleRunDetail,
    ConsoleRunSummary,
    ConsoleSchedulesPage,
    ConsoleScheduleSummary,
    ConsoleScheduleTriggerSummary,
    ConsoleSessionSummary,
    ConsoleSnapshotSummary,
    ConsoleTapeEntrySummary,
    ConsoleTapeInfo,
    ConsoleTopicAnchorSummary,
    ConsoleTopicCostSummary,
    ConsoleTopicDetail,
    ConsoleTopicRecallSummary,
    ConsoleTopicSummary,
    ConsoleValidationOutcomeSummary,
    ConsoleWorkspaceCapabilitySummary,
    ConsoleWorkspaceSummary,
    message_label,
    render_console_actions_page,
    render_console_bee_page,
    render_console_context_page,
    render_console_interactions_page,
    render_console_memory_page,
    render_console_observability_page,
    render_console_page,
    render_console_release_page,
    render_console_run_detail_page,
    render_console_runs_page,
    render_console_schedules_page,
    render_console_sessions_page,
    render_console_tape_page,
    render_console_topic_detail_page,
    render_console_topics_page,
    render_console_workspaces_page,
    safe_error_summary,
    safe_id_value,
    safe_key_tuple,
    safe_label_value,
    safe_text_value,
)
from coding_agent.runs import (
    CloudWorkspaceRef,
    ExternalWorkerExecutorRef,
    IsolationPolicy,
    LocalAttachedExecutorRef,
    ManagedPoolExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
    run_target_from_dict,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    ApprovalResponseSchema,
    ApproveRequest,
    CancelSessionResponse,
    CheckpointCaptureRequest,
    CheckpointListResponse,
    CheckpointMetadataResponse,
    CheckpointRestoreResponse,
    CloseSessionResponse,
    CreateSessionRequest,
    DisplayEventResponse,
    DisplayEventsResponse,
    HealthResponse,
    PromptRequest,
    PublishSessionRequest,
    PublishSessionResponse,
    ReadinessResponse,
    ResolveInteractionRequest,
    ResumeSessionRequest,
    RuntimeConfigUpdateRequest,
    RuntimeConfigUpdateResponse,
    RuntimeEventResponse,
    RuntimeEventsResponse,
    RuntimeInteractionListResponse,
    RuntimeInteractionResponse,
    RuntimeMessageSnapshotResponse,
    RuntimeRunListResponse,
    RuntimeRunResponse,
    SessionListResponse,
    SessionResponse,
    SessionResultResponse,
    SessionSummaryResponse,
    WorkspaceArchiveManifestResponse,
    WorkspaceArchiveResponse,
    WorkspaceCleanupResponse,
    WorkspaceDiffFileSchema,
    WorkspaceDiffResponse,
    WorkspaceGcResponse,
    WorkspaceListResponse,
    WorkspacePatchResponse,
    WorkspaceRetentionPolicy,
    WorkspaceRetentionRequest,
    WorkspaceRetentionResponse,
    WorkspaceSummarySchema,
    WorkspaceUnpinRequest,
    ExecutorListResponse,
    WorkerClaimRequest,
    WorkerClaimResponse,
    WorkerApprovalRequest,
    WorkerApprovalResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerListResponse,
    WorkerRunCompleteRequest,
    WorkerRuntimeEventsRequest,
    WorkerStatusResponse,
)
from coding_agent.server.session_manager import Session, SessionManager
from coding_agent.server.stores.session_owner_store import (
    SQLiteSessionOwnerStore,
    SessionOwnershipConflictError,
    SessionOwnershipConflictReason,
    SessionOwnerStore,
)
from coding_agent.server.stores.workspace_store import (
    JSONValue,
    PGWorkspaceMetadataStore,
    WorkspaceRecord,
)
from coding_agent.verification.release_manifest import (
    load_release_verification_manifest,
)
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
from coding_agent.wire.protocol import (
    CompletionStatus,
    ThinkingDelta,
    ToolResultDelta,
    TurnStatusDelta,
)

logger = logging.getLogger(__name__)

# Constants
APPROVAL_TIMEOUT_SECONDS = 120
SESSION_IDLE_TIMEOUT_MINUTES = 30
WORKER_STALE_AFTER_SECONDS = 60
WORKER_OFFLINE_AFTER_SECONDS = 300
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


def _load_observability_config() -> dict[str, Any]:
    return _load_agent_config_section("observability")


def _load_bee_workspace_config() -> dict[str, Any]:
    return _load_agent_config_section("bee_workspace")


def _prometheus_metrics_enabled() -> bool:
    try:
        config = _load_observability_config()
    except Exception:
        logger.exception("Unable to load observability metrics config")
        return False
    if config.get("enabled") is not True:
        return False
    metrics_config = config.get("metrics")
    if not isinstance(metrics_config, Mapping):
        return False
    if metrics_config.get("enabled") is not True:
        return False
    return metrics_config.get("endpoint_enabled", True) is not False


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


def _cleanup_provisioned_cloud_binding(workspace: CloudWorkspaceRef) -> None:
    cloud_workspace_config = _load_cloud_workspace_config()
    provider = cloud_workspace_config.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        return
    cleanup_cloud_binding_from_config(cloud_workspace_config, workspace)


def _session_uses_attached_executor(session: Any) -> bool:
    target = getattr(session, "default_run_target", None)
    if not isinstance(target, RunTarget):
        return False
    return isinstance(
        target.executor, (ExternalWorkerExecutorRef, LocalAttachedExecutorRef)
    )


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


def _storage_uses_local_sqlite_bundle(storage_config: dict[str, Any]) -> bool:
    backend_keys = (
        "http_session_backend",
        "tape_backend",
        "checkpoint_backend",
        "runtime_backend",
    )
    if any(
        str(storage_config.get(key, "")).strip().lower() != "sqlite"
        for key in backend_keys
    ):
        return False
    local_path = normalize_storage_path(
        str(local_sqlite_path_from_storage_config(storage_config))
    )
    path_keys = (
        "http_session_path",
        "tape_path",
        "checkpoint_path",
        "runtime_path",
    )
    return all(
        normalize_storage_path(str(storage_config.get(key, ""))) == local_path
        for key in path_keys
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
    uses_local_sqlite_bundle = _storage_uses_local_sqlite_bundle(storage_config)
    manager = SessionManager(
        storage_config=storage_config,
        owner_store=(
            SQLiteSessionOwnerStore(
                local_sqlite_path_from_storage_config(storage_config)
            )
            if uses_local_sqlite_bundle
            else None
        ),
        owner_id=(
            _configured_owner_id(storage_config) if uses_local_sqlite_bundle else None
        ),
        fencing_token=1 if uses_local_sqlite_bundle else None,
        owner_lease_seconds=(
            _configured_owner_lease_seconds(storage_config)
            if uses_local_sqlite_bundle
            else 30.0
        ),
        cloud_workspace_client_factory=(
            cloud_client_factory_from_config(_load_cloud_workspace_config())
            if _load_cloud_workspace_config().get("enabled") is True
            else None
        ),
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
        target = session.default_run_target
        if target is None:
            continue
        workspace = target.workspace
        if isinstance(workspace, CloudWorkspaceRef):
            active_workspace_ids.add(workspace.workspace_id)
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
    try:
        recovered = await session_manager.recover_stale_runtime_runs()
        if recovered:
            logger.info("Recovered %s stale runtime run(s)", recovered)
    except Exception:
        logger.exception("Failed to recover stale runtime runs during startup")
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


class _HTTPMetricsASGIMiddleware:
    def __init__(self, wrapped_app: Any) -> None:
        self._wrapped_app = wrapped_app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._wrapped_app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_with_metrics(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                message_status = message.get("status")
                if isinstance(message_status, int):
                    status_code = message_status
            await send(message)

        try:
            await self._wrapped_app(scope, receive, send_with_metrics)
        finally:
            if _prometheus_metrics_enabled():
                route = scope.get("route")
                route_label = getattr(route, "path", None)
                if not isinstance(route_label, str) or not route_label:
                    route_label = "unmatched"
                route_label = _http_metrics_route_label(route_label)
                record_http_request_metric(
                    method=cast(str, scope["method"]),
                    route=route_label,
                    status_code=status_code,
                    duration_ms=(time.perf_counter() - start) * 1000,
                )


app.add_middleware(_HTTPMetricsASGIMiddleware)


# Add exception handler for rate limit exceeded
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    raise HTTPException(status_code=429, detail=str(exc))


@app.get("/console", response_class=HTMLResponse)
async def console_overview(request: Request) -> HTMLResponse:
    del request
    return HTMLResponse(render_console_page("/console"))


@app.get("/console/sessions", response_class=HTMLResponse)
async def console_sessions(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    sessions: list[ConsoleSessionSummary] = []
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        summary = session.as_dict()
        sessions.append(
            ConsoleSessionSummary(
                session_id=session.id,
                status=str(summary["status"]),
                turn_status=str(summary["turn_status"]),
                created_at=session.created_at,
                updated_at=session.last_activity,
                current_turn_id=session.current_turn_id,
            )
        )
    sessions.sort(key=lambda item: item.updated_at, reverse=True)
    return HTMLResponse(render_console_sessions_page(sessions))


@app.get("/console/runs", response_class=HTMLResponse)
async def console_runs(
    request: Request,
    status: str | None = Query(None, min_length=1, max_length=80),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    runs: list[ConsoleRunSummary] = []
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        try:
            session_runs = await session_manager.list_runtime_runs(session_id)
        except RuntimeError:
            session_runs = []
        for run in session_runs:
            if status is not None and run.status != status:
                continue
            runs.append(
                ConsoleRunSummary(
                    run_id=run.run_id,
                    session_id=run.session_id,
                    status=run.status,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    error_summary=safe_error_summary(run.error),
                )
            )
    runs.sort(key=lambda item: item.started_at, reverse=True)
    return HTMLResponse(render_console_runs_page(runs, status_filter=status))


@app.get("/console/runs/{run_id}", response_class=HTMLResponse)
async def console_run_detail(
    request: Request,
    run_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    run = await _get_visible_runtime_run(run_id, auth_context)
    try:
        snapshot_record = await session_manager.load_runtime_message_snapshot(run_id)
    except (KeyError, RuntimeError):
        snapshot = None
    else:
        snapshot = ConsoleSnapshotSummary(
            snapshot_id=snapshot_record.snapshot_id,
            message_count=len(snapshot_record.messages),
            created_at=snapshot_record.created_at,
            message_labels=tuple(
                message_label(message) for message in snapshot_record.messages
            ),
            metadata_keys=safe_key_tuple(snapshot_record.metadata),
        )
    try:
        events = await session_manager.replay_display_events(run_id, limit=1000)
    except (KeyError, RuntimeError):
        events = []
    event_summaries = tuple(
        ConsoleDisplayEventSummary(
            sequence=event.sequence,
            source_event_id=event.source_event_id,
            display_kind=event.display_kind,
            created_at=event.created_at,
            payload_keys=safe_key_tuple(event.payload),
        )
        for event in sorted(
            events, key=lambda item: (item.sequence or 0, item.created_at)
        )
    )
    detail = ConsoleRunDetail(
        run_id=run.run_id,
        session_id=run.session_id,
        tape_id=run.tape_id,
        parent_run_id=run.parent_run_id,
        agent_id=run.agent_id,
        status=run.status,
        started_at=run.started_at,
        ended_at=run.ended_at,
        error_summary=safe_error_summary(run.error),
        metadata_keys=safe_key_tuple(run.metadata),
        result_keys=safe_key_tuple(run.result),
        snapshot=snapshot,
        events=event_summaries,
    )
    return HTMLResponse(render_console_run_detail_page(detail))


@app.get("/console/interactions", response_class=HTMLResponse)
async def console_interactions(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    interactions: list[ConsoleInteractionSummary] = []
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        try:
            runs = await session_manager.list_runtime_runs(session_id)
        except RuntimeError:
            runs = []
        for run in runs:
            try:
                run_interactions = await session_manager.list_runtime_interactions(
                    run.run_id
                )
            except RuntimeError:
                run_interactions = []
            for interaction in run_interactions:
                interactions.append(
                    ConsoleInteractionSummary(
                        interaction_id=interaction.interaction_id,
                        run_id=interaction.run_id,
                        session_id=run.session_id,
                        tool_call_id=safe_id_value(
                            interaction.metadata.get("tool_call_id")
                        ),
                        interaction_kind=interaction.interaction_kind,
                        status=interaction.status,
                        created_at=interaction.created_at,
                        resolved_at=interaction.resolved_at,
                    )
                )
    interactions.sort(key=lambda item: item.created_at, reverse=True)
    return HTMLResponse(render_console_interactions_page(interactions))


@app.get("/console/tape", response_class=HTMLResponse)
async def console_tape(
    request: Request,
    tape_id: str | None = Query(None, min_length=1, max_length=200),
    kind: str | None = Query(None, min_length=1, max_length=80),
    run_id: str | None = Query(None, min_length=1, max_length=200),
    tool_call_id: str | None = Query(None, min_length=1, max_length=200),
    anchor_type: str | None = Query(None, min_length=1, max_length=80),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if (
        run_id is not None
        and auth_context is not None
        and auth_context.scope != "admin"
    ):
        try:
            visible_run = await _get_visible_runtime_run(run_id, auth_context)
        except HTTPException as exc:
            if exc.status_code == 404:
                return HTMLResponse(render_console_tape_page(None, []))
            raise
        if visible_run.tape_id is not None:
            if tape_id is not None and tape_id != visible_run.tape_id:
                return HTMLResponse(render_console_tape_page(None, []))
            tape_id = visible_run.tape_id
    visible_tape_ids = await _visible_console_tape_ids(auth_context)
    if not _can_search_tape(
        auth_context=auth_context,
        tape_id=tape_id,
        run_id=run_id,
        visible_tape_ids=visible_tape_ids,
    ):
        return HTMLResponse(render_console_tape_page(None, []))
    if (
        auth_context is not None
        and auth_context.scope != "admin"
        and tape_id is None
        and run_id is None
    ):
        entries = []
        for visible_tape_id in sorted(visible_tape_ids):
            entries.extend(
                await session_manager.search_tape_debug_entries(
                    tape_id=visible_tape_id,
                    kind=kind,
                    run_id=None,
                    tool_call_id=tool_call_id,
                    anchor_type=anchor_type,
                    limit=100,
                )
            )
        return HTMLResponse(
            render_console_tape_page(
                None,
                [_tape_entry_summary(entry) for entry in entries],
            )
        )
    info = None
    if tape_id is not None:
        tape_info = await session_manager.load_tape_debug_info(tape_id)
        if tape_info is not None:
            info = ConsoleTapeInfo(
                tape_id=tape_info.tape_id,
                entry_count=tape_info.entry_count,
                first_seq=tape_info.first_seq,
                last_seq=tape_info.last_seq,
            )
    entries = await session_manager.search_tape_debug_entries(
        tape_id=tape_id,
        kind=kind,
        run_id=run_id,
        tool_call_id=tool_call_id,
        anchor_type=anchor_type,
        limit=100,
    )
    summaries = [_tape_entry_summary(entry) for entry in entries]
    return HTMLResponse(render_console_tape_page(info, summaries))


@app.get("/console/context", response_class=HTMLResponse)
async def console_context(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if run_id is None:
        return HTMLResponse(render_console_context_page(None))
    try:
        run = await _get_visible_runtime_run(run_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            return HTMLResponse(render_console_context_page(None))
        raise
    return HTMLResponse(render_console_context_page(_context_summary_from_run(run)))


@app.get("/console/memory", response_class=HTMLResponse)
async def console_memory(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if run_id is None:
        runs = await _visible_console_runs(auth_context)
        return HTMLResponse(render_console_memory_page(_memory_summary_from_runs(runs)))
    try:
        run = await _get_visible_runtime_run(run_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            return HTMLResponse(render_console_memory_page(None))
        raise
    return HTMLResponse(render_console_memory_page(_memory_summary_from_run(run)))


@app.get("/console/actions", response_class=HTMLResponse)
async def console_actions(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    if run_id is None:
        return HTMLResponse(render_console_actions_page(None))
    try:
        run = await _get_visible_runtime_run(run_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            return HTMLResponse(render_console_actions_page(None))
        raise
    return HTMLResponse(
        render_console_actions_page(_action_validation_summary_from_run(run))
    )


@app.get("/console/observability", response_class=HTMLResponse)
async def console_observability(
    request: Request,
    run_id: str | None = Query(None, min_length=1, max_length=200),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    correlation = None
    if run_id is not None:
        try:
            run = await _get_visible_runtime_run(run_id, auth_context)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            correlation = _correlation_summary_from_run(run)
    return HTMLResponse(
        render_console_observability_page(
            _observability_summary(correlation=correlation)
        )
    )


@app.get("/console/topics", response_class=HTMLResponse)
async def console_topics(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    return HTMLResponse(
        render_console_topics_page(await _console_topic_summaries(auth_context))
    )


@app.get("/console/topics/{topic_id}", response_class=HTMLResponse)
async def console_topic_detail(
    request: Request,
    topic_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    detail = await _console_topic_detail(topic_id, auth_context)
    if detail is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return HTMLResponse(render_console_topic_detail_page(detail))


@app.get("/console/schedules", response_class=HTMLResponse)
async def console_schedules(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    return HTMLResponse(
        render_console_schedules_page(await _console_schedules_page(auth_context))
    )


@app.get("/console/bee", response_class=HTMLResponse)
async def console_bee(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    return HTMLResponse(render_console_bee_page(await _console_bee_page(auth_context)))


@app.get("/console/workspaces", response_class=HTMLResponse)
async def console_workspaces(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> HTMLResponse:
    del request
    _require_admin_context(auth_context)
    return HTMLResponse(
        render_console_workspaces_page(
            await _console_workspace_summaries(),
            _console_workspace_capability_summary(),
        )
    )


@app.get("/console/release", response_class=HTMLResponse)
async def console_release(request: Request) -> HTMLResponse:
    del request
    return HTMLResponse(render_console_release_page(await _release_summary()))


def _http_metrics_route_label(route_label: str) -> str:
    if route_label == "/console/topics/{topic_id}":
        return "/console/topics/detail"
    return route_label


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
        result_refs=record.result_refs,
        is_local=(
            local_provider_instance_id is not None
            and record.provider_instance_id == local_provider_instance_id
        ),
    )


async def _local_workspace_record_for_provider_operation(
    workspace_id: str,
) -> WorkspaceRecord:
    record = await session_manager.load_workspace_record_by_workspace_id(workspace_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Workspace not found: {workspace_id}"
        )
    local_provider_instance_id = _configured_provider_instance_id()
    if local_provider_instance_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "cloud_workspace.provider_instance_id is required for "
                "provider-local workspace operations"
            ),
        )
    if record.provider_instance_id != local_provider_instance_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Workspace belongs to a different provider instance and cannot "
                "be operated by this server"
            ),
        )
    return record


def _retention_expires_at(
    *,
    retention_policy: WorkspaceRetentionPolicy,
    ttl_seconds: int | None,
) -> datetime | None:
    if retention_policy != "ttl":
        return None
    if ttl_seconds is None:
        configured_ttl = _load_remote_retention_config().get("default_ttl_seconds")
        if not isinstance(configured_ttl, int) or configured_ttl <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "ttl_seconds is required when retention_policy=ttl and "
                    "remote_retention.default_ttl_seconds is not configured"
                ),
            )
        ttl_seconds = configured_ttl
    return datetime.now(UTC) + timedelta(seconds=ttl_seconds)


async def _update_workspace_retention(
    workspace_id: str,
    *,
    retention_policy: WorkspaceRetentionPolicy,
    ttl_seconds: int | None,
) -> WorkspaceRetentionResponse:
    record = await session_manager.load_workspace_record_by_workspace_id(workspace_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Workspace not found: {workspace_id}"
        )
    expires_at = _retention_expires_at(
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
    )
    await session_manager.update_workspace_record_retention(
        record.workspace_record_id,
        retention_policy=retention_policy,
        expires_at=expires_at,
        status="retained",
    )
    return WorkspaceRetentionResponse(
        workspace_id=record.workspace_id,
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
        status="retained",
    )


async def _persist_workspace_publication_refs(
    session_id: str,
    *,
    publication: WorkspaceBranchPublication,
    mode: Literal["branch", "pr"],
    pr_url: str | None,
) -> None:
    if not _remote_retention_enabled():
        return
    session = await session_manager.get_session_async(session_id)
    target = session.default_run_target
    if target is None:
        return
    workspace = target.workspace
    if not isinstance(workspace, CloudWorkspaceRef):
        return
    record = await session_manager.load_workspace_record_by_workspace_id(
        workspace.workspace_id
    )
    if record is None:
        return
    result_refs = dict(record.result_refs)
    result_refs["publication"] = {
        "mode": mode,
        "status": publication.status,
        "branch_name": publication.branch_name,
        "pushed_ref": publication.pushed_ref,
        "commit_sha": publication.commit_sha,
        "remote_url": publication.remote_url,
        "pr_url": pr_url,
        "error": publication.error,
        "artifact_ref": _artifact_ref_json(
            _workspace_publication_artifact_ref(
                session_id=session_id,
                publication=publication,
                mode=mode,
                pr_url=pr_url,
            )
        ),
    }
    await session_manager.update_workspace_record_result_refs(
        record.workspace_record_id,
        result_refs=result_refs,
    )


def _workspace_publication_artifact_ref(
    *,
    session_id: str,
    publication: WorkspaceBranchPublication,
    mode: Literal["branch", "pr"],
    pr_url: str | None,
) -> ArtifactRef:
    metadata: dict[str, object] = {
        "session_id": session_id,
        "workspace_id": publication.workspace_id,
        "mode": mode,
        "status": publication.status,
        "branch_name": publication.branch_name,
        "pushed_ref": publication.pushed_ref,
        "commit_sha": publication.commit_sha,
        "remote_url": publication.remote_url,
        "pr_url": pr_url,
        "error": publication.error,
    }
    artifact_kind: Literal["branch", "pull_request"] = (
        "pull_request" if pr_url is not None else "branch"
    )
    uri = pr_url if pr_url is not None else publication.remote_url
    summary = _workspace_publication_artifact_summary(publication)
    return ArtifactRef(
        artifact_id=f"workspace:{publication.workspace_id}:publication",
        kind=artifact_kind,
        title="Workspace publication",
        summary=summary,
        uri=uri,
        metadata=metadata,
    )


def _workspace_publication_artifact_summary(
    publication: WorkspaceBranchPublication,
) -> str:
    if publication.status == "published" and publication.branch_name:
        if publication.commit_sha:
            return f"Published branch {publication.branch_name} at {publication.commit_sha}"
        return f"Published branch {publication.branch_name}"
    if publication.status == "partial" and publication.commit_sha:
        return f"Created local publication commit {publication.commit_sha}"
    return f"Workspace publication {publication.status}"


def _artifact_ref_json(artifact_ref: ArtifactRef) -> dict[str, JSONValue]:
    return {
        "artifact_id": artifact_ref.artifact_id,
        "kind": artifact_ref.kind,
        "title": artifact_ref.title,
        "summary": artifact_ref.summary,
        "uri": artifact_ref.uri,
        "metadata": cast(dict[str, JSONValue], artifact_ref.metadata),
        "producer_turn_id": artifact_ref.producer_turn_id,
    }


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


def _explicit_run_target_from_request(
    body: CreateSessionRequest | None,
) -> RunTarget | None:
    if body is None:
        return None
    if body.default_run_target is not None and body.run_target is not None:
        raise ValueError("default_run_target and run_target cannot both be set")
    target_payload = (
        body.default_run_target
        if body.default_run_target is not None
        else body.run_target
    )
    if target_payload is None:
        return None
    return run_target_from_dict(target_payload)


def _provisioned_run_target_from_request(
    body: CreateSessionRequest | None,
) -> RunTarget | None:
    if body is None:
        return None
    explicit_target = _explicit_run_target_from_request(body)
    if explicit_target is not None and body.workspace_source is not None:
        raise ValueError("run_target and workspace_source cannot be set together")
    if explicit_target is not None:
        return explicit_target
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
    workspace = provision_cloud_binding_from_config(
        cloud_workspace_config,
        body.workspace_source.model_dump(mode="python"),
    )
    return RunTarget(
        workspace=workspace,
        executor=ManagedPoolExecutorRef(),
        isolation=IsolationPolicy(
            kind="provider_sandbox",
            network="provider_managed",
            filesystem="provider_managed",
            secrets="provider_managed",
        ),
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
    target: RunTarget | None,
    auth_context: AuthContext | None = None,
) -> dict[str, str]:
    origin = {
        "channel": "http",
        "placement_kind": "local_path" if target is None else target.workspace.kind,
        "executor_kind": "local_daemon" if target is None else target.executor.kind,
    }
    if body is not None and body.workspace_source is not None:
        origin["workspace_source_kind"] = body.workspace_source.kind
    if target is not None and isinstance(target.workspace, CloudWorkspaceRef):
        cloud_workspace_config = _load_cloud_workspace_config()
        provider = cloud_workspace_config.get("provider")
        provider_instance_id = cloud_workspace_config.get("provider_instance_id")
        workspace_root_ref = cloud_workspace_config.get("workspace_root")
        workspace_host_label = cloud_workspace_config.get("workspace_host_label")
        if isinstance(provider, str) and provider.strip():
            origin["workspace_provider"] = provider.strip()
        if isinstance(provider_instance_id, str) and provider_instance_id.strip():
            origin["provider_instance_id"] = provider_instance_id.strip()
        if isinstance(workspace_root_ref, str) and workspace_root_ref.strip():
            origin["workspace_root_ref"] = workspace_root_ref.strip()
        if isinstance(workspace_host_label, str) and workspace_host_label.strip():
            origin["workspace_host_label"] = workspace_host_label.strip()
        elif isinstance(provider_instance_id, str) and provider_instance_id.strip():
            origin["workspace_host_label"] = provider_instance_id.strip()
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
    result = session.broadcast_event_nowait(event)

    if result.full_pruned_count:
        logger.info(
            "Pruned %d full event queue(s) for session %s",
            result.full_pruned_count,
            session.id,
        )
    if result.failed_pruned_count:
        logger.info(
            "Pruned %d failed event queue(s) for session %s",
            result.failed_pruned_count,
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


def _legacy_event_stream_transform(event: dict[str, str]) -> dict[str, str] | None:
    return event


def _display_event_stream_transform(
    session: Session,
    event: dict[str, str],
) -> dict[str, str] | None:
    projector = DisplayEventStreamProjector(
        session_id=session.id,
        current_run_id=lambda: session.current_turn_id,
    )
    return projector.project(event)


async def _owned_session_event_generator(
    session_id: str,
    queue: asyncio.Queue[dict[str, str]],
    transform_event: Callable[[dict[str, str]], dict[str, str] | None],
) -> AsyncIterator[dict[str, str]]:
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                try:
                    await session_manager.verify_event_stream_ownership(session_id)
                except SessionOwnershipConflictError:
                    break
                outbound_event = transform_event(event)
                if outbound_event is not None:
                    yield outbound_event
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
                yield {"event": "ping", "data": ""}
    except asyncio.CancelledError:
        raise
    finally:
        await _cleanup_event_queue_on_disconnect(session_id, queue)


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


@app.get("/metrics", response_class=PlainTextResponse)
@limiter.limit(RateLimits.HEALTH)
async def metrics_endpoint(request: Request) -> PlainTextResponse:
    if not _prometheus_metrics_enabled():
        raise HTTPException(status_code=404, detail="Metrics endpoint disabled")
    return PlainTextResponse(
        prometheus_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
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
    provisioned_workspace: CloudWorkspaceRef | None = None

    try:
        default_run_target = _provisioned_run_target_from_request(body)
        if (
            body is not None
            and body.workspace_source is not None
            and default_run_target is not None
            and isinstance(default_run_target.workspace, CloudWorkspaceRef)
        ):
            provisioned_workspace = default_run_target.workspace
        session_id = await session_manager.create_session(
            repo_path=repo_path,
            origin=_session_origin_from_request(
                body,
                default_run_target,
                auth_context,
            ),
            default_run_target=default_run_target,
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
        if provisioned_workspace is not None:
            try:
                await asyncio.to_thread(
                    _cleanup_provisioned_cloud_binding, provisioned_workspace
                )
            except Exception:
                logger.exception("Failed to roll back provisioned cloud workspace")
        raise
    except Exception as exc:
        if provisioned_workspace is not None:
            try:
                await asyncio.to_thread(
                    _cleanup_provisioned_cloud_binding, provisioned_workspace
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


@app.patch("/sessions/{session_id}/runtime-config", response_model=RuntimeConfigUpdateResponse)
@limiter.limit(RateLimits.CREATE_SESSION)
async def update_runtime_config(
    request: Request,
    session_id: str,
    body: RuntimeConfigUpdateRequest,
    api_key: str | None = Depends(verify_api_key),
) -> RuntimeConfigUpdateResponse:
    """Update the session runtime provider/model/base_url config in-place.

    Applies changes next turn. The session's tape and history are preserved.
    Returns 409 if a turn is currently in progress.
    Returns 400 if no config fields are provided.
    """
    if body.model is None and body.provider is None and body.base_url is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of model, provider, or base_url must be provided",
        )

    try:
        session = await session_manager.get_session_async(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if getattr(session, "turn_in_progress", False):
        raise HTTPException(status_code=409, detail="Turn already in progress")

    try:
        updated_session = await session_manager.replace_session_runtime_config(
            session_id,
            model_name=body.model,
            provider_name=body.provider,
            base_url=body.base_url,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(
                status_code=409, detail="Turn already in progress"
            ) from exc
        raise HTTPException(
            status_code=500, detail=_http_exception_detail(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=_http_exception_detail(exc)
        ) from exc

    return RuntimeConfigUpdateResponse(
        session_id=session_id,
        provider_name=getattr(updated_session, "provider_name", None),
        model_name=getattr(updated_session, "model_name", None),
        base_url=getattr(updated_session, "base_url", None),
    )


@app.post("/sessions/{session_id}/prompt")
@limiter.limit(RateLimits.SEND_PROMPT)
async def send_prompt(
    request: Request,
    session_id: str,
    body: PromptRequest | None = None,
    prompt: str | None = None,  # Backward compat: query param
    event_format: Literal["wire", "display"] = Query("wire"),
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

    if _session_uses_attached_executor(session):
        return await _send_attached_executor_prompt(session_id, prompt_text)

    session.turn_in_progress = True
    session.last_activity = datetime.now(UTC)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        """Generate SSE events for the turn."""
        try:
            session.task = asyncio.create_task(
                session_manager.run_agent(session_id, prompt_text)
            )

            async for event in stream_wire_messages(session.wire, session.task):
                await _broadcast_event(session, event)
                response_event = _prompt_stream_event_response(
                    session,
                    event,
                    event_format=event_format,
                )
                if response_event is not None:
                    yield response_event

        except Exception as e:
            logger.exception("Error during turn")
            error_data = {
                "event": "Error",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "error": str(e),
                        "timestamp": datetime.now(UTC).isoformat(),
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
            session.last_activity = datetime.now(UTC)

    # Return SSE stream from wire
    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@app.post("/sessions/{session_id}/resume")
@limiter.limit(RateLimits.SEND_PROMPT)
async def resume_session(
    request: Request,
    session_id: str,
    body: ResumeSessionRequest | None = None,
    event_format: Literal["wire", "display"] = Query("wire"),
    api_key: str | None = Depends(verify_api_key),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> EventSourceResponse:
    del request, api_key
    try:
        session = await session_manager.get_session_async(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if not _auth_context_can_access_session(auth_context, session):
        raise HTTPException(status_code=404, detail="Session not found")

    prompt_text = None if body is None else body.prompt
    resume_reason = "user_resume" if body is None else body.resume_reason

    if _session_uses_attached_executor(session):
        try:
            run = await session_manager.resume_session(
                session_id,
                prompt=prompt_text,
                resume_reason=resume_reason,
            )
        except RuntimeError as exc:
            detail = str(exc)
            if detail in {
                "turn already in progress",
                "latest run is still active",
                "session has no previous run to resume",
            }:
                raise HTTPException(status_code=409, detail=detail) from exc
            raise HTTPException(status_code=503, detail=detail) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        async def external_event_generator() -> AsyncIterator[dict[str, str]]:
            yield {
                "event": "RunRequested",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "run_id": run.run_id,
                        "status": run.status,
                        "previous_run_id": run.parent_run_id,
                        "resume_from_run_id": run.metadata.get("resume_from_run_id"),
                        "resume_from_event_id": run.metadata.get(
                            "resume_from_event_id"
                        ),
                    }
                ),
            }

        return EventSourceResponse(
            external_event_generator(),
            media_type="text/event-stream",
        )

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            session.task = asyncio.create_task(
                session_manager.resume_session(
                    session_id,
                    prompt=prompt_text,
                    resume_reason=resume_reason,
                )
            )
            async for event in stream_wire_messages(session.wire, session.task):
                await _broadcast_event(session, event)
                response_event = _prompt_stream_event_response(
                    session,
                    event,
                    event_format=event_format,
                )
                if response_event is not None:
                    yield response_event
        except Exception as exc:
            logger.exception("Error during session resume")
            error_data = {
                "event": "Error",
                "data": json.dumps(
                    {
                        "session_id": session_id,
                        "error": str(exc),
                        "timestamp": datetime.now(UTC).isoformat(),
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
            session.last_activity = datetime.now(UTC)

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


def _prompt_stream_event_response(
    session: Session,
    event: dict[str, str],
    *,
    event_format: Literal["wire", "display"],
) -> dict[str, str] | None:
    if event_format == "wire":
        return event
    if event.get("event") == "Error":
        return event
    return _display_event_stream_transform(session, event)


async def _send_attached_executor_prompt(
    session_id: str,
    prompt_text: str,
) -> EventSourceResponse:
    try:
        run = await session_manager.request_attached_executor_run(
            session_id,
            prompt_text,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except SessionOwnershipConflictError as exc:
        raise _owner_conflict_http_exception(exc, session_id=session_id) from exc
    except RuntimeError as exc:
        if str(exc) == "turn already in progress":
            raise HTTPException(
                status_code=409,
                detail="Turn already in progress",
            ) from exc
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_requested_event = {
        "event": "RunRequested",
        "data": json.dumps(
            {
                "session_id": session_id,
                "run_id": run.run_id,
                "status": run.status,
            }
        ),
    }

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        yield run_requested_event

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@app.post("/worker/runs/claim", response_model=WorkerClaimResponse)
@app.post("/executor/runs/claim", response_model=WorkerClaimResponse)
@limiter.limit(RateLimits.SEND_PROMPT)
async def claim_worker_run(
    request: Request,
    body: WorkerClaimRequest,
    api_key: str | None = Depends(verify_api_key),
) -> WorkerClaimResponse:
    try:
        claim = await session_manager.claim_attached_executor_run(
            executor_id=body.worker_id,
            executor_kind=body.executor_kind,
            session_id=body.session_id,
            lease_seconds=body.lease_seconds,
            worker_instance_id=body.worker_instance_id,
            process_id=body.process_id,
            capabilities=cast(JSONObject | None, body.capabilities),
            workspace_sync=cast(JSONObject | None, body.workspace_sync),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if claim is None:
        raise HTTPException(status_code=404, detail="No worker run available")
    session = claim.session
    return WorkerClaimResponse(
        run_id=claim.run.run_id,
        session_id=claim.run.session_id,
        claim_token=claim.claim_token,
        prompt=claim.prompt,
        tape_id=claim.run.tape_id,
        approval_policy=session.approval_policy.value,
        provider_name=session.provider_name,
        model_name=session.model_name,
        base_url=session.base_url,
        max_steps=session.max_steps,
    )


@app.post("/worker/runs/{run_id}/heartbeat", response_model=WorkerHeartbeatResponse)
@app.post("/executor/runs/{run_id}/heartbeat", response_model=WorkerHeartbeatResponse)
@limiter.limit(RateLimits.SEND_PROMPT)
async def heartbeat_worker_run(
    request: Request,
    run_id: str,
    body: WorkerHeartbeatRequest,
    api_key: str | None = Depends(verify_api_key),
) -> WorkerHeartbeatResponse:
    try:
        run = await session_manager.heartbeat_attached_executor_run(
            run_id=run_id,
            executor_id=body.worker_id,
            claim_token=body.claim_token,
            lease_seconds=body.lease_seconds,
            worker_instance_id=body.worker_instance_id,
            process_id=body.process_id,
            capabilities=cast(JSONObject | None, body.capabilities),
            workspace_sync=cast(JSONObject | None, body.workspace_sync),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkerHeartbeatResponse(
        run_id=run.run_id,
        status=run.status,
        cancel_requested=bool(run.metadata.get("cancel_requested_at")),
    )


@app.post("/worker/runs/{run_id}/events", response_model=RuntimeEventsResponse)
@app.post("/executor/runs/{run_id}/events", response_model=RuntimeEventsResponse)
@limiter.limit(RateLimits.SEND_PROMPT)
async def append_worker_run_events(
    request: Request,
    run_id: str,
    body: WorkerRuntimeEventsRequest,
    api_key: str | None = Depends(verify_api_key),
) -> RuntimeEventsResponse:
    records: list[RuntimeEventRecord] = []
    try:
        run = await session_manager.load_runtime_run(run_id)
        session = await session_manager.get_session_async(run.session_id)
        for event in body.events:
            payload = cast(
                dict[str, JSONValue],
                {
                    "message_type": event.event,
                    "message": dict(event.data),
                },
            )
            record = await session_manager.append_attached_executor_event(
                run_id=run_id,
                executor_id=body.worker_id,
                claim_token=body.claim_token,
                event_id=event.event_id,
                event_kind=f"wire.{event.event}",
                payload=payload,
                created_at=event.created_at or datetime.now(UTC),
            )
            records.append(record)
            await _broadcast_event(
                session,
                {"event": event.event, "data": json.dumps(event.data)},
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RuntimeEventsResponse(
        run_id=run_id,
        events=[_runtime_event_response(record) for record in records],
    )


@app.post("/worker/runs/{run_id}/approval", response_model=WorkerApprovalResponse)
@app.post("/executor/runs/{run_id}/approval", response_model=WorkerApprovalResponse)
@limiter.limit(RateLimits.APPROVE)
async def request_worker_approval(
    request: Request,
    run_id: str,
    body: WorkerApprovalRequest,
    api_key: str | None = Depends(verify_api_key),
) -> WorkerApprovalResponse:
    try:
        run = await session_manager.heartbeat_attached_executor_run(
            run_id=run_id,
            executor_id=body.worker_id,
            claim_token=body.claim_token,
        )
        approval = await wait_for_approval(
            run.session_id,
            ApprovalRequest(
                session_id=run.session_id,
                agent_id="",
                request_id=body.request_id,
                tool=body.tool_name,
                args=body.arguments,
                timeout_seconds=body.timeout_seconds,
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkerApprovalResponse(
        request_id=approval.request_id,
        approved=approval.approved,
        feedback=approval.feedback,
        scope=approval.scope,
    )


@app.post("/worker/runs/{run_id}/complete", response_model=RuntimeRunResponse)
@app.post("/executor/runs/{run_id}/complete", response_model=RuntimeRunResponse)
@limiter.limit(RateLimits.SEND_PROMPT)
async def complete_worker_run(
    request: Request,
    run_id: str,
    body: WorkerRunCompleteRequest,
    api_key: str | None = Depends(verify_api_key),
) -> RuntimeRunResponse:
    try:
        run = await session_manager.finalize_attached_executor_run(
            run_id=run_id,
            executor_id=body.worker_id,
            claim_token=body.claim_token,
            status=body.status,
            result=cast(dict[str, JSONValue], dict(body.result)),
            error=body.error,
            tape_id=body.tape_id,
            tape_entries=(
                None
                if body.tape_entries is None
                else [
                    cast(dict[str, JSONValue], dict(entry))
                    for entry in body.tape_entries
                ]
            ),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    completion_status = _worker_completion_status(body.status)
    try:
        session = await session_manager.get_session_async(run.session_id)
        await _broadcast_event(
            session,
            {
                "event": "TurnEnd",
                "data": json.dumps(
                    {
                        "session_id": run.session_id,
                        "agent_id": "",
                        "turn_id": run.run_id,
                        "completion_status": completion_status,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ),
            },
        )
    except Exception:
        logger.exception(
            "Failed to broadcast external worker completion",
            extra={"run_id": run.run_id, "session_id": run.session_id},
        )
    return _runtime_run_response(run)


def _worker_completion_status(status: str) -> str:
    if status == "completed":
        return CompletionStatus.COMPLETED.value
    if status == "cancelled":
        return CompletionStatus.BLOCKED.value
    return CompletionStatus.ERROR.value


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

    return EventSourceResponse(
        _owned_session_event_generator(
            session_id,
            queue,
            _legacy_event_stream_transform,
        )
    )


@app.get("/sessions/{session_id}/display-events")
@limiter.limit(RateLimits.EVENTS)
async def get_session_display_events(
    request: Request,
    session_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> EventSourceResponse:
    """Persistent SSE stream of projected user-facing display events."""
    del request, api_key
    try:
        session = await session_manager.get_session_async(session_id)
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

    return EventSourceResponse(
        _owned_session_event_generator(
            session_id,
            queue,
            lambda event: _display_event_stream_transform(session, event),
        )
    )


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
                    {
                        "session_id": session_id,
                        "turn_id": result.turn_id,
                    }
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
    del request
    _require_admin_context(auth_context)
    if not _remote_retention_enabled():
        raise _durable_workspace_retention_not_implemented()
    return await _update_workspace_retention(
        workspace_id,
        retention_policy=body.retention_policy,
        ttl_seconds=body.ttl_seconds,
    )


@app.post("/workspaces/{workspace_id}/pin", response_model=WorkspaceRetentionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def pin_workspace(
    request: Request,
    workspace_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request
    _require_admin_context(auth_context)
    if not _remote_retention_enabled():
        raise _durable_workspace_retention_not_implemented()
    return await _update_workspace_retention(
        workspace_id,
        retention_policy="pinned",
        ttl_seconds=None,
    )


@app.post("/workspaces/{workspace_id}/unpin", response_model=WorkspaceRetentionResponse)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def unpin_workspace(
    request: Request,
    workspace_id: str,
    body: WorkspaceUnpinRequest | None = None,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkspaceRetentionResponse:
    del request
    _require_admin_context(auth_context)
    if not _remote_retention_enabled():
        raise _durable_workspace_retention_not_implemented()
    retention_policy: Literal["delete_on_close", "ttl"]
    ttl_seconds: int | None
    if body is not None and body.retention_policy is not None:
        retention_policy = body.retention_policy
        ttl_seconds = body.ttl_seconds
    else:
        default_policy = _load_remote_retention_config().get("default_policy")
        if default_policy == "ttl":
            retention_policy = "ttl"
            ttl_seconds = None if body is None else body.ttl_seconds
        else:
            retention_policy = "delete_on_close"
            ttl_seconds = None
    return await _update_workspace_retention(
        workspace_id,
        retention_policy=retention_policy,
        ttl_seconds=ttl_seconds,
    )


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
    if _remote_retention_enabled():
        record = await _local_workspace_record_for_provider_operation(workspace_id)
        await session_manager.update_workspace_record_status(
            record.workspace_record_id,
            status="cleaning",
            cleanup_error=None,
        )
        try:
            entry = await asyncio.to_thread(
                cleanup_cloud_workspace_from_config,
                _load_cloud_workspace_config(),
                workspace_id,
                active_workspace_ids=await _active_cloud_workspace_ids(),
            )
        except KeyError as exc:
            await session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="lost",
                cleanup_error=str(exc),
            )
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            await session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="cleanup_failed",
                cleanup_error=str(exc),
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "Cloud workspace cleanup failed workspace_id=%s", workspace_id
            )
            await session_manager.update_workspace_record_status(
                record.workspace_record_id,
                status="cleanup_failed",
                cleanup_error=str(exc) or "workspace cleanup failed",
            )
            response.status_code = 500
            return WorkspaceCleanupResponse(
                workspace_id=workspace_id,
                status="cleanup_failed",
                error=str(exc),
            )
        await session_manager.update_workspace_record_status(
            record.workspace_record_id,
            status="cleaned",
            cleanup_error=None,
        )
        return WorkspaceCleanupResponse(
            workspace_id=entry.workspace_id,
            status="cleaned",
        )
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
    if _remote_retention_enabled():
        _ = await _local_workspace_record_for_provider_operation(workspace_id)
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
    if _remote_retention_enabled():
        _ = await _local_workspace_record_for_provider_operation(workspace_id)
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
        summaries.append(await _session_summary_response(session))
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

    return await _session_summary_response(session)


async def _session_summary_response(session: Session) -> SessionSummaryResponse:
    payload = session.as_dict()
    payload.update(await session_manager.session_resume_metadata(session.id))
    return SessionSummaryResponse(**payload)


@app.get("/sessions/{session_id}/runs", response_model=RuntimeRunListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_session_runtime_runs(
    request: Request,
    session_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeRunListResponse:
    del request
    _ = await _get_visible_session(session_id, auth_context)
    try:
        records = await session_manager.list_runtime_runs(session_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime store not configured",
        ) from exc
    return RuntimeRunListResponse(
        session_id=session_id,
        runs=[_runtime_run_response(record) for record in records],
    )


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


def _runtime_run_response(record: AgentRunRecord) -> RuntimeRunResponse:
    metadata = dict(record.metadata)
    metadata.pop("claim_token_hash", None)
    return RuntimeRunResponse(
        run_id=record.run_id,
        session_id=record.session_id,
        tape_id=record.tape_id,
        parent_run_id=record.parent_run_id,
        agent_id=record.agent_id,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        metadata=metadata,
        result=record.result,
        error=record.error,
    )


def _runtime_message_snapshot_response(
    record: RunMessageSnapshotRecord,
) -> RuntimeMessageSnapshotResponse:
    return RuntimeMessageSnapshotResponse(
        snapshot_id=record.snapshot_id,
        run_id=record.run_id,
        messages=record.messages,
        metadata=record.metadata,
        created_at=record.created_at,
    )


def _runtime_event_response(record: RuntimeEventRecord) -> RuntimeEventResponse:
    return RuntimeEventResponse(
        sequence=record.sequence,
        event_id=record.event_id,
        run_id=record.run_id,
        event_kind=record.event_kind,
        payload=record.payload,
        created_at=record.created_at,
    )


def _display_event_response(record: DisplayEvent) -> DisplayEventResponse:
    return DisplayEventResponse(
        source_event_id=record.source_event_id,
        run_id=record.run_id,
        sequence=record.sequence,
        display_kind=record.display_kind,
        payload=record.payload,
        created_at=record.created_at,
    )


def _runtime_interaction_response(
    record: AgentInteractionRecord,
) -> RuntimeInteractionResponse:
    return RuntimeInteractionResponse(
        interaction_id=record.interaction_id,
        run_id=record.run_id,
        interaction_kind=record.interaction_kind,
        status=record.status,
        request_payload=record.request_payload,
        response_payload=record.response_payload,
        metadata=record.metadata,
        created_at=record.created_at,
        resolved_at=record.resolved_at,
    )


def _safe_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _metadata_datetime(
    metadata: Mapping[str, object],
    key: str,
) -> datetime | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def _visible_runtime_runs(
    auth_context: AuthContext | None,
) -> list[AgentRunRecord]:
    records: list[AgentRunRecord] = []
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        try:
            records.extend(await session_manager.list_runtime_runs(session_id))
        except RuntimeError:
            continue
    return records


def _worker_status_from_runs(
    worker_id: str,
    runs: Iterable[AgentRunRecord],
) -> WorkerStatusResponse:
    worker_runs = [run for run in runs if run.metadata.get("worker_id") == worker_id]
    if not worker_runs:
        raise KeyError(f"worker not found: {worker_id}")
    worker_runs.sort(key=lambda run: (run.started_at, run.run_id))
    latest = worker_runs[-1]
    now = datetime.now(UTC)
    active_runs = [
        run for run in worker_runs if run.status in {"claimed", "running", "cancelling"}
    ]
    active_runs.sort(key=lambda run: (run.started_at, run.run_id))
    current = active_runs[-1] if active_runs else None
    source = current or latest
    metadata = source.metadata
    lease_expires_at = _metadata_datetime(metadata, "lease_expires_at")
    last_seen_at = (
        _metadata_datetime(metadata, "last_heartbeat_at")
        or _metadata_datetime(metadata, "claimed_at")
        or _metadata_datetime(metadata, "finalized_at")
        or latest.ended_at
        or latest.started_at
    )
    status: Literal["idle", "running", "stale", "offline"]
    last_seen_age_seconds = (now - last_seen_at).total_seconds()
    if current is None and last_seen_age_seconds > float(WORKER_OFFLINE_AFTER_SECONDS):
        status = "offline"
    elif current is None:
        status = "idle"
    elif lease_expires_at is not None and lease_expires_at <= now:
        status = "stale"
    elif lease_expires_at is None and last_seen_age_seconds > float(
        WORKER_OFFLINE_AFTER_SECONDS
    ):
        status = "offline"
    elif lease_expires_at is None and last_seen_age_seconds > float(
        WORKER_STALE_AFTER_SECONDS
    ):
        status = "stale"
    else:
        status = "running"
    workspace_ref = metadata.get("workspace_ref")
    capabilities = metadata.get("capabilities")
    workspace_sync = metadata.get("workspace_sync")
    process_id = metadata.get("process_id")
    return WorkerStatusResponse(
        worker_id=worker_id,
        executor_id=worker_id,
        status=status,
        executor_kind=(
            metadata.get("executor_kind")
            if isinstance(metadata.get("executor_kind"), str)
            else None
        ),
        worker_pool=(
            metadata.get("worker_pool")
            if isinstance(metadata.get("worker_pool"), str)
            else None
        ),
        worker_instance_id=(
            metadata.get("worker_instance_id")
            if isinstance(metadata.get("worker_instance_id"), str)
            else None
        ),
        process_id=process_id if isinstance(process_id, int) else None,
        capabilities=capabilities if isinstance(capabilities, dict) else None,
        workspace_ref=workspace_ref if isinstance(workspace_ref, dict) else None,
        workspace_sync=workspace_sync if isinstance(workspace_sync, dict) else None,
        current_run_id=current.run_id if current is not None else None,
        current_session_id=current.session_id if current is not None else None,
        last_run_id=latest.run_id,
        last_session_id=latest.session_id,
        last_seen_at=last_seen_at,
        lease_expires_at=lease_expires_at,
    )


def _worker_statuses_from_runs(
    runs: Iterable[AgentRunRecord],
) -> list[WorkerStatusResponse]:
    run_list = list(runs)
    worker_ids = {
        worker_id
        for run in run_list
        if isinstance(worker_id := run.metadata.get("worker_id"), str) and worker_id
    }
    return sorted(
        (_worker_status_from_runs(worker_id, run_list) for worker_id in worker_ids),
        key=lambda worker: worker.worker_id,
    )


def _tape_entry_summary(result: object) -> ConsoleTapeEntrySummary:
    entry = _safe_dict(getattr(result, "entry", {}))
    payload = _safe_dict(entry.get("payload"))
    meta = _safe_dict(entry.get("meta"))
    kind = entry.get("kind")
    return ConsoleTapeEntrySummary(
        tape_id=str(getattr(result, "tape_id", "")),
        seq=int(getattr(result, "seq", 0)),
        kind=kind if isinstance(kind, str) else "-",
        run_id=safe_id_value(payload.get("run_id") or meta.get("run_id")),
        tool_call_id=safe_id_value(
            payload.get("tool_call_id") or meta.get("tool_call_id")
        ),
        anchor_type=safe_id_value(meta.get("anchor_type")),
        payload_keys=safe_key_tuple(payload),
        meta_keys=safe_key_tuple(meta),
    )


def _context_summary_from_run(run: AgentRunRecord) -> ConsoleContextSummary | None:
    raw_pack = run.metadata.get("context_pack")
    if not isinstance(raw_pack, dict):
        return ConsoleContextSummary(run_id=run.run_id, sections=())
    raw_sections = raw_pack.get("sections")
    if not isinstance(raw_sections, list):
        return ConsoleContextSummary(run_id=run.run_id, sections=())
    sections: list[ConsoleContextSectionSummary] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict):
            continue
        title = safe_text_value(raw_section.get("title")) or "Context"
        raw_items = raw_section.get("items")
        if not isinstance(raw_items, list):
            continue
        items = tuple(
            item
            for raw_item in raw_items
            if isinstance(raw_item, dict)
            for item in [_context_evidence_from_item(raw_item)]
            if item is not None
        )
        if items:
            sections.append(ConsoleContextSectionSummary(title=title, items=items))
    return ConsoleContextSummary(run_id=run.run_id, sections=tuple(sections))


def _context_evidence_from_item(
    raw_item: dict[str, object],
) -> ConsoleContextEvidence | None:
    source_id = safe_text_value(raw_item.get("source_id"))
    label = safe_text_value(raw_item.get("label"))
    source_kind = safe_text_value(raw_item.get("source_kind"))
    if source_id is None or label is None or source_kind is None:
        return None
    evidence_reason = None
    raw_evidence = raw_item.get("evidence")
    if isinstance(raw_evidence, list) and raw_evidence:
        first = raw_evidence[0]
        if isinstance(first, dict):
            evidence_reason = safe_text_value(first.get("label"))
    score_raw = raw_item.get("score")
    score = float(score_raw) if isinstance(score_raw, int | float) else None
    return ConsoleContextEvidence(
        kind=source_kind,
        label=label,
        source_id=source_id,
        repo_path=safe_text_value(raw_item.get("repo_path")),
        line_start=_optional_int(raw_item.get("line_start")),
        line_end=_optional_int(raw_item.get("line_end")),
        score=score,
        reason=evidence_reason,
    )


def _memory_summary_from_runs(runs: Iterable[AgentRunRecord]) -> ConsoleMemorySummary:
    items: list[ConsoleMemoryEvidence] = []
    reviews: list[ConsoleMemoryReviewSummary] = []
    seen_items: set[tuple[str | None, str]] = set()
    seen_reviews: set[tuple[str | None, str]] = set()
    for run in runs:
        summary = _memory_summary_from_run(run)
        for item in summary.items:
            key = (item.run_id, item.source_id)
            if key in seen_items:
                continue
            seen_items.add(key)
            items.append(item)
        for review in summary.reviews:
            key = (review.run_id, review.source_id)
            if key in seen_reviews:
                continue
            seen_reviews.add(key)
            reviews.append(review)
    return ConsoleMemorySummary(
        run_id=None,
        items=tuple(items),
        reviews=tuple(reviews),
    )


def _memory_summary_from_run(run: AgentRunRecord) -> ConsoleMemorySummary:
    items: list[ConsoleMemoryEvidence] = []
    reviews: list[ConsoleMemoryReviewSummary] = []
    seen_source_ids: set[str] = set()
    context = _context_summary_from_run(run)
    if context is not None:
        for section in context.sections:
            for item in section.items:
                if item.kind != "memory":
                    continue
                if item.source_id in seen_source_ids:
                    continue
                seen_source_ids.add(item.source_id)
                items.append(
                    ConsoleMemoryEvidence(
                        run_id=run.run_id,
                        source_id=item.source_id,
                        label="Memory",
                        status="context_pack",
                        tags_count=None,
                        evidence_count=None,
                        repo_path=item.repo_path,
                        line_start=item.line_start,
                        line_end=item.line_end,
                    )
                )
    for raw_item in _metadata_lists(
        run.metadata,
        ("memory_evidence", "memory_candidates", "memories"),
    ):
        review = _memory_review_from_item(run.run_id, raw_item)
        if review is not None:
            reviews.append(review)
        memory = _memory_evidence_from_item(run.run_id, raw_item)
        if memory is None or memory.source_id in seen_source_ids:
            continue
        seen_source_ids.add(memory.source_id)
        items.append(memory)
    return ConsoleMemorySummary(
        run_id=run.run_id,
        items=tuple(items),
        reviews=tuple(
            sorted(
                reviews,
                key=lambda item: (
                    item.status,
                    item.kind,
                    item.source_id,
                ),
            )
        ),
    )


def _memory_evidence_from_item(
    run_id: str,
    raw_item: dict[str, object],
) -> ConsoleMemoryEvidence | None:
    source_id = (
        safe_id_value(raw_item.get("source_id"))
        or safe_id_value(raw_item.get("memory_id"))
        or safe_id_value(raw_item.get("id"))
    )
    if source_id is None:
        return None
    label = safe_text_value(raw_item.get("label")) or "Memory"
    evidence = raw_item.get("evidence")
    tags = raw_item.get("tags")
    return ConsoleMemoryEvidence(
        run_id=run_id,
        source_id=source_id,
        label=label,
        status=safe_label_value(raw_item.get("status")),
        tags_count=len(tags) if isinstance(tags, list) else None,
        evidence_count=len(evidence) if isinstance(evidence, list) else None,
        repo_path=safe_text_value(raw_item.get("repo_path")),
        line_start=_optional_int(raw_item.get("line_start")),
        line_end=_optional_int(raw_item.get("line_end")),
    )


def _memory_review_from_item(
    run_id: str,
    raw_item: dict[str, object],
) -> ConsoleMemoryReviewSummary | None:
    source_id = (
        safe_id_value(raw_item.get("candidate_id"))
        or safe_id_value(raw_item.get("source_id"))
        or safe_id_value(raw_item.get("memory_id"))
        or safe_id_value(raw_item.get("id"))
    )
    if source_id is None:
        return None
    status = safe_label_value(raw_item.get("status")) or "candidate"
    if status not in {"candidate", "accepted", "rejected", "archived"}:
        status = "redacted"
    provenance = _safe_dict(raw_item.get("provenance"))
    source_ranges = provenance.get("source_entry_ranges")
    evidence_refs = provenance.get("evidence_refs") or raw_item.get("evidence")
    return ConsoleMemoryReviewSummary(
        source_id=source_id,
        label=safe_text_value(raw_item.get("title") or raw_item.get("label"))
        or "Memory",
        kind=safe_label_value(raw_item.get("kind")) or "unknown",
        status=status,
        run_id=safe_id_value(run_id),
        topic_id=safe_id_value(provenance.get("topic_id") or raw_item.get("topic_id")),
        task_id=safe_id_value(provenance.get("task_id") or raw_item.get("task_id")),
        evidence_count=len(evidence_refs) if isinstance(evidence_refs, list) else None,
        source_range_count=len(source_ranges)
        if isinstance(source_ranges, list)
        else None,
    )


def _action_validation_summary_from_run(
    run: AgentRunRecord,
) -> ConsoleActionValidationSummary:
    actions = tuple(
        action
        for raw_item in _metadata_lists(run.metadata, ("actions", "action_summaries"))
        for action in [_action_summary_from_item(run.run_id, raw_item)]
        if action is not None
    )
    validation_report = _safe_dict(
        run.metadata.get("validation_report") or run.metadata.get("validation")
    )
    validations = tuple(_validation_outcomes(validation_report))
    validation_status = safe_label_value(validation_report.get("status"))
    return ConsoleActionValidationSummary(
        run_id=run.run_id,
        actions=actions,
        validation_status=validation_status,
        validations=validations,
    )


def _correlation_summary_from_run(run: AgentRunRecord) -> ConsoleCorrelationSummary:
    action = _first_metadata_item(run.metadata, ("actions", "action_summaries"))
    return ConsoleCorrelationSummary(
        session_id=safe_id_value(run.session_id),
        run_id=safe_id_value(run.run_id),
        tape_id=safe_id_value(run.tape_id),
        topic_id=_topic_id_from_run(run),
        retrieval_id=safe_id_value(
            run.metadata.get("retrieval_id") or run.metadata.get("context_retrieval_id")
        ),
        action_id=(
            safe_id_value(action.get("action_id") or action.get("id"))
            if action is not None
            else safe_id_value(run.metadata.get("action_id"))
        ),
        validation_id=(
            safe_id_value(action.get("validation_id"))
            if action is not None
            else safe_id_value(run.metadata.get("validation_id"))
        ),
        interaction_id=(
            safe_id_value(
                action.get("interaction_id") or action.get("approval_interaction_id")
            )
            if action is not None
            else safe_id_value(run.metadata.get("interaction_id"))
        ),
    )


async def _console_topic_summaries(
    auth_context: AuthContext | None,
) -> list[ConsoleTopicSummary]:
    store_summaries = await _console_topic_summaries_from_store(auth_context)
    if store_summaries:
        return store_summaries
    runs = await _visible_console_runs(auth_context)
    summaries_by_topic: dict[str, ConsoleTopicSummary] = {}
    run_counts: dict[str, int] = {}
    for run in runs:
        summary = _topic_summary_from_run(run)
        if summary is None:
            continue
        run_counts[summary.topic_id] = run_counts.get(summary.topic_id, 0) + 1
        if summary.topic_id not in summaries_by_topic:
            summaries_by_topic[summary.topic_id] = summary
    summaries = [
        ConsoleTopicSummary(
            topic_id=summary.topic_id,
            tape_id=summary.tape_id,
            session_id=summary.session_id,
            kind=summary.kind,
            status=summary.status,
            title=summary.title,
            summary=summary.summary,
            topic_initial_seq=summary.topic_initial_seq,
            topic_finalized_seq=summary.topic_finalized_seq,
            run_count=run_counts[summary.topic_id],
            cost_total_tokens=summary.cost_total_tokens,
        )
        for summary in summaries_by_topic.values()
    ]
    summaries.sort(key=lambda item: (item.session_id or "", item.topic_id))
    return summaries


async def _console_topic_detail(
    topic_id: str,
    auth_context: AuthContext | None,
) -> ConsoleTopicDetail | None:
    safe_topic_id = safe_id_value(topic_id)
    if safe_topic_id is None:
        return None
    store_detail = await _console_topic_detail_from_store(safe_topic_id, auth_context)
    if store_detail is not None:
        return store_detail
    runs = [
        run
        for run in await _visible_console_runs(auth_context)
        if _topic_id_from_run(run) == safe_topic_id
    ]
    if not runs:
        return None
    base_summary = _topic_summary_from_run(runs[0])
    if base_summary is None:
        return None
    summary = ConsoleTopicSummary(
        topic_id=base_summary.topic_id,
        tape_id=base_summary.tape_id,
        session_id=base_summary.session_id,
        kind=base_summary.kind,
        status=base_summary.status,
        title=base_summary.title,
        summary=base_summary.summary,
        topic_initial_seq=base_summary.topic_initial_seq,
        topic_finalized_seq=base_summary.topic_finalized_seq,
        run_count=len(runs),
        cost_total_tokens=base_summary.cost_total_tokens,
    )
    actions: list[ConsoleActionSummary] = []
    validations: list[ConsoleValidationOutcomeSummary] = []
    run_summaries: list[ConsoleRunSummary] = []
    for run in runs:
        run_summaries.append(_console_run_summary_from_run(run))
        action_summary = _action_validation_summary_from_run(run)
        actions.extend(action_summary.actions)
        validations.extend(action_summary.validations)
    return ConsoleTopicDetail(
        summary=summary,
        anchors=tuple(
            anchor for run in runs for anchor in _topic_anchor_summaries_from_run(run)
        ),
        recalls=tuple(
            recall for run in runs for recall in _topic_recall_summaries_from_run(run)
        ),
        cost=_topic_cost_summary_from_run(runs[0]),
        runs=tuple(run_summaries),
        actions=tuple(actions),
        validations=tuple(validations),
    )


async def _console_topic_summaries_from_store(
    auth_context: AuthContext | None,
) -> list[ConsoleTopicSummary]:
    store = _console_topic_store()
    if store is None:
        return []
    topics: list[TopicRecord] = []
    try:
        if auth_context is None or auth_context.scope == "admin":
            topics = await store.list_topics(limit=100)
        else:
            for session_id in await _visible_console_session_ids(auth_context):
                topics.extend(await store.list_topics(session_id=session_id, limit=100))
    except Exception:
        logger.exception(
            "Console topic store list failed; falling back to run metadata"
        )
        return []
    summaries = [
        _topic_summary_from_record(topic, await store.load_topic_cost(topic.topic_id))
        for topic in topics
    ]
    summaries.sort(key=lambda item: (item.session_id or "", item.topic_id))
    return summaries


async def _console_topic_detail_from_store(
    topic_id: str,
    auth_context: AuthContext | None,
) -> ConsoleTopicDetail | None:
    store = _console_topic_store()
    if store is None:
        return None
    try:
        topic = await store.load_topic(topic_id)
    except Exception:
        logger.exception(
            "Console topic store load failed; falling back to run metadata"
        )
        return None
    if topic is None:
        return None
    if not await _auth_context_can_access_topic(auth_context, topic):
        return None
    try:
        anchors = tuple(
            _topic_anchor_summary_from_record(anchor)
            for anchor in await store.list_topic_anchors(topic.topic_id)
        )
        recalls = tuple(
            _topic_recall_summary_from_record(recall)
            for recall in await store.list_recall_links(topic.topic_id)
        )
        cost = await store.load_topic_cost(topic.topic_id)
    except Exception:
        logger.exception(
            "Console topic store detail failed; falling back to run metadata"
        )
        return None
    runs = [
        run
        for run in await _visible_console_runs(auth_context)
        if _topic_id_from_run(run) == topic.topic_id
    ]
    run_summaries = tuple(_console_run_summary_from_run(run) for run in runs)
    actions: list[ConsoleActionSummary] = []
    validations: list[ConsoleValidationOutcomeSummary] = []
    for run in runs:
        action_summary = _action_validation_summary_from_run(run)
        actions.extend(action_summary.actions)
        validations.extend(action_summary.validations)
    return ConsoleTopicDetail(
        summary=_topic_summary_from_record(topic, cost),
        anchors=anchors,
        recalls=recalls,
        cost=_topic_cost_summary_from_record(cost),
        runs=run_summaries,
        actions=tuple(actions),
        validations=tuple(validations),
    )


async def _console_schedules_page(
    auth_context: AuthContext | None,
) -> ConsoleSchedulesPage:
    store = _console_scheduled_run_store()
    if store is None:
        return ConsoleSchedulesPage(schedules=(), triggers=(), signals=())
    try:
        visible_session_ids = await _visible_console_session_ids(auth_context)
        if auth_context is None or auth_context.scope == "admin":
            schedules = await store.list_schedules(limit=100)
            signals = await store.list_signals(limit=100)
        else:
            schedules = []
            signals = []
            for session_id in visible_session_ids:
                schedules.extend(await store.list_schedules(session_id=session_id))
                signals.extend(await store.list_signals(session_id=session_id))
        triggers: list[ScheduleTriggerRecord] = []
        for schedule in schedules[:100]:
            triggers.extend(await store.list_triggers(schedule.schedule_id, limit=25))
        for signal in signals[:100]:
            triggers.extend(
                await store.list_triggers(f"signal:{signal.signal_id}", limit=25)
            )
    except Exception:
        logger.exception(
            "Console scheduled run store failed; rendering empty schedule page"
        )
        return ConsoleSchedulesPage(schedules=(), triggers=(), signals=())
    return ConsoleSchedulesPage(
        schedules=tuple(
            _schedule_summary_from_record(schedule) for schedule in schedules
        ),
        triggers=tuple(
            _schedule_trigger_summary_from_record(trigger) for trigger in triggers
        ),
        signals=tuple(
            _proactive_signal_summary_from_record(signal) for signal in signals
        ),
    )


async def _console_bee_page(auth_context: AuthContext | None) -> ConsoleBeePage:
    runs = await _visible_console_runs(auth_context)
    nodes = tuple(node for run in runs for node in _bee_node_summaries_from_run(run))
    launch_summaries = await _bee_launch_summaries(auth_context, runs)
    tasks_by_id: dict[str, ConsoleBeeTaskSummary] = {}
    for node in nodes:
        current = tasks_by_id.get(node.task_id)
        if current is None:
            tasks_by_id[node.task_id] = ConsoleBeeTaskSummary(
                task_id=node.task_id,
                topic_id=node.topic_id,
                session_id=node.session_id,
                kind=node.task_kind,
                profile=node.task_profile,
                status=node.status,
                node_count=1,
                run_count=1 if node.run_id else 0,
            )
            continue
        tasks_by_id[node.task_id] = ConsoleBeeTaskSummary(
            task_id=current.task_id,
            topic_id=current.topic_id,
            session_id=current.session_id,
            kind=current.kind,
            profile=current.profile,
            status=_combined_bee_status(current.status, node.status),
            node_count=current.node_count + 1,
            run_count=current.run_count + (1 if node.run_id else 0),
        )
    can_view_workspace_artifacts = _can_view_global_console_artifacts(auth_context)
    return ConsoleBeePage(
        tasks=tuple(sorted(tasks_by_id.values(), key=lambda item: item.task_id)),
        nodes=tuple(sorted(nodes, key=lambda item: (item.task_id, item.node_id))),
        launches=launch_summaries,
        executor_runs=await _executor_run_summaries(auth_context, runs),
        packs=(_console_bee_pack_summaries() if can_view_workspace_artifacts else ()),
        pack_templates=(
            _console_bee_pack_template_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        pack_compatibility=(
            _console_bee_pack_compatibility_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        pack_dry_runs=(
            _console_bee_pack_dry_run_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        templates=(
            _console_bee_workspace_template_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        run_artifacts=(
            _console_bee_workspace_run_artifact_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
        commands=(
            _console_bee_workspace_command_summaries()
            if can_view_workspace_artifacts
            else ()
        ),
    )


async def _executor_run_summaries(
    auth_context: AuthContext | None,
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleExecutorRunSummary, ...]:
    summaries = {
        summary.executor_run_id: summary
        for summary in await _executor_run_summaries_from_store(auth_context, runs)
    }
    for summary in _executor_run_summaries_from_runs(runs):
        summaries.setdefault(summary.executor_run_id, summary)
    return tuple(sorted(summaries.values(), key=lambda item: item.executor_run_id))


async def _executor_run_summaries_from_store(
    auth_context: AuthContext | None,
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleExecutorRunSummary, ...]:
    store = _console_executor_run_store()
    if store is None:
        return ()
    visible_task_ids = {
        task_id
        for run in runs
        if (task_id := safe_id_value(run.metadata.get("task_id"))) is not None
    }
    try:
        records = await store.list_executor_runs(limit=100)
    except Exception:
        logger.exception("Console executor run store list failed")
        return ()
    if auth_context is not None and auth_context.scope != "admin":
        records = [record for record in records if record.task_id in visible_task_ids]
    return tuple(_executor_run_summary_from_record(record) for record in records)


def _executor_run_summary_from_record(
    record: ExecutorRunRecord,
) -> ConsoleExecutorRunSummary:
    return ConsoleExecutorRunSummary(
        executor_run_id=safe_id_value(record.executor_run_id) or "unknown",
        executor_kind=safe_label_value(record.executor_kind) or "unknown",
        status=safe_label_value(record.status) or "unknown",
        task_id=safe_id_value(record.task_id),
        node_id=safe_id_value(record.node_id),
        launch_id=safe_id_value(record.launch_id),
        topic_id=safe_id_value(record.topic_id),
        capability_status=safe_label_value(record.metadata.get("capability_status")),
        sanitized_summary=safe_error_summary(record.sanitized_summary),
    )


def _executor_run_summaries_from_runs(
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleExecutorRunSummary, ...]:
    summaries: dict[str, ConsoleExecutorRunSummary] = {}
    for run in runs:
        metadata = run.metadata
        executor_run_id = safe_id_value(metadata.get("executor_run_id"))
        executor_kind = safe_label_value(metadata.get("executor_kind"))
        if executor_run_id is None or executor_kind is None:
            continue
        summaries[executor_run_id] = ConsoleExecutorRunSummary(
            executor_run_id=executor_run_id,
            executor_kind=executor_kind,
            status=safe_label_value(metadata.get("executor_status")) or run.status,
            task_id=safe_id_value(metadata.get("task_id")),
            node_id=safe_id_value(metadata.get("node_id")),
            launch_id=safe_id_value(metadata.get("launch_id")),
            topic_id=safe_id_value(metadata.get("topic_id")),
            capability_status=safe_label_value(metadata.get("executor_capability")),
            sanitized_summary=safe_error_summary(metadata.get("executor_summary")),
        )
    return tuple(sorted(summaries.values(), key=lambda item: item.executor_run_id))


async def _bee_launch_summaries(
    auth_context: AuthContext | None,
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleBeeLaunchSummary, ...]:
    launch_summaries = {
        launch.launch_id: launch
        for launch in await _bee_launch_summaries_from_store(auth_context)
    }
    for launch in _bee_launch_summaries_from_runs(runs):
        launch_summaries.setdefault(launch.launch_id, launch)
    return tuple(sorted(launch_summaries.values(), key=lambda item: item.launch_id))


async def _bee_launch_summaries_from_store(
    auth_context: AuthContext | None,
) -> tuple[ConsoleBeeLaunchSummary, ...]:
    store = _console_bee_launch_store()
    if store is None:
        return ()
    launches: list[BeeLaunchRecord] = []
    try:
        if auth_context is None or auth_context.scope == "admin":
            launches = await store.list_launches(limit=100)
        else:
            for session_id in await _visible_console_session_ids(auth_context):
                launches.extend(
                    await store.list_launches(session_id=session_id, limit=100)
                )
    except Exception:
        logger.exception(
            "Console Bee launch store list failed; falling back to run metadata"
        )
        return ()
    return tuple(_bee_launch_summary_from_record(launch) for launch in launches)


def _bee_launch_summary_from_record(
    launch: BeeLaunchRecord,
) -> ConsoleBeeLaunchSummary:
    return ConsoleBeeLaunchSummary(
        launch_id=safe_id_value(launch.launch_id),
        source=safe_label_value(launch.source),
        status=safe_label_value(launch.status),
        template_id=safe_id_value(launch.template_id),
        task_id=safe_id_value(launch.task_id),
        topic_id=safe_id_value(launch.topic_id),
        schedule_id=safe_id_value(launch.schedule_id),
        signal_id=safe_id_value(launch.signal_id),
        error_summary=safe_error_summary(launch.error_message or launch.error_type),
    )


def _bee_launch_summaries_from_runs(
    runs: Iterable[AgentRunRecord],
) -> tuple[ConsoleBeeLaunchSummary, ...]:
    launches: dict[str, ConsoleBeeLaunchSummary] = {}
    for run in runs:
        metadata = run.metadata
        launch_id = safe_id_value(metadata.get("launch_id"))
        if not launch_id:
            continue
        launch_source = safe_label_value(metadata.get("launch_source"))
        if launch_source not in {"manual", "schedule", "proactive_signal"}:
            continue
        launches[launch_id] = ConsoleBeeLaunchSummary(
            launch_id=launch_id,
            source=launch_source,
            status=safe_label_value(metadata.get("launch_status")) or run.status,
            template_id=safe_id_value(metadata.get("template_id")),
            task_id=safe_id_value(metadata.get("task_id")),
            topic_id=safe_id_value(metadata.get("topic_id")),
            schedule_id=safe_id_value(metadata.get("schedule_id")),
            signal_id=safe_id_value(metadata.get("signal_id")),
            error_summary=safe_error_summary(metadata.get("launch_error")),
        )
    return tuple(sorted(launches.values(), key=lambda item: item.launch_id))


def _can_view_global_console_artifacts(auth_context: AuthContext | None) -> bool:
    return auth_context is None or auth_context.scope == "admin"


def _console_bee_workspace_root() -> Path | None:
    config = _load_bee_workspace_config()
    root = config.get("workspace_root")
    if not isinstance(root, str) or not root.strip():
        return None
    return Path(root).expanduser().resolve()


def _console_bee_workspace_templates() -> tuple[BeeWorkspaceTemplate, ...]:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return ()
    try:
        return tuple(discover_bee_workspace_templates(workspace_root))
    except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
        logger.warning(
            "Console Bee workspace template discovery failed; rendering empty summaries",
            exc_info=exc,
        )
        return ()


def _console_bee_pack_registry() -> BeePackRegistry | None:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return None
    try:
        return BeePackRegistry.discover(
            (workspace_root,),
            source=BeeTemplatePackSource.LOCAL_WORKSPACE,
        )
    except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
        logger.warning(
            "Console Bee template pack discovery failed; rendering empty summaries",
            exc_info=exc,
        )
        return None


def _console_bee_pack_summaries() -> tuple[ConsoleBeePackSummary, ...]:
    registry = _console_bee_pack_registry()
    if registry is None:
        return ()
    return tuple(
        ConsoleBeePackSummary(
            pack_id=summary.pack_id,
            name=safe_text_value(summary.name) or "untitled",
            version=safe_label_value(summary.version) or "unknown",
            source_type=summary.source.value,
            domain_profile=safe_label_value(summary.domain_profile),
            tags=tuple(
                tag
                for tag in (safe_label_value(tag) for tag in summary.tags)
                if tag is not None
            ),
            template_count=summary.template_count,
        )
        for summary in registry.list_packs()
    )


def _console_bee_pack_template_summaries() -> tuple[ConsoleBeePackTemplateSummary, ...]:
    registry = _console_bee_pack_registry()
    if registry is None:
        return ()
    summaries: list[ConsoleBeePackTemplateSummary] = []
    for pack in registry.list_packs():
        for template in registry.list_templates(pack.pack_id):
            summaries.append(
                ConsoleBeePackTemplateSummary(
                    pack_id=template.pack_id,
                    template_id=template.template_id,
                    source_type=template.source.value,
                    kind=safe_label_value(template.template_kind) or "unknown",
                    profile=safe_label_value(template.template_profile) or "unknown",
                    title=safe_text_value(template.title) or "untitled",
                )
            )
    return tuple(sorted(summaries, key=lambda item: (item.pack_id, item.template_id)))


def _console_bee_pack_compatibility_summaries() -> tuple[
    ConsoleBeePackCompatibilitySummary, ...
]:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return ()
    try:
        report = validate_bee_pack_compatibility(
            workspace_root,
            source=BeeTemplatePackSource.LOCAL_WORKSPACE,
        )
    except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
        logger.warning(
            "Console Bee template pack compatibility failed; rendering empty summaries",
            exc_info=exc,
        )
        return ()
    record_bee_pack_validation_metric(
        status=report.status,
        source_type=report.source.value,
    )
    for template in report.templates:
        record_bee_pack_template_metric(
            status=template.status,
            source_type=report.source.value,
        )
    return (
        ConsoleBeePackCompatibilitySummary(
            pack_id=safe_id_value(report.pack_id),
            source_type=report.source.value,
            status=safe_label_value(report.status) or "unknown",
            check_count=len(report.checks),
            finding_count=len(report.findings),
            template_count=len(report.templates),
            recommended_fixes=tuple(
                fix
                for fix in (
                    safe_text_value(finding.recommended_fix)
                    for finding in report.findings[:5]
                )
                if fix is not None
            ),
        ),
    )


def _console_bee_pack_dry_run_summaries() -> tuple[ConsoleBeePackDryRunSummary, ...]:
    registry = _console_bee_pack_registry()
    if registry is None:
        return ()
    summaries: list[ConsoleBeePackDryRunSummary] = []
    for pack in registry.list_packs():
        for template in registry.list_templates(pack.pack_id):
            try:
                plan = build_bee_pack_dry_run_plan(
                    registry,
                    pack_id=pack.pack_id,
                    template_id=template.template_id,
                    inputs={},
                )
            except (OSError, ValueError, TypeError, FileNotFoundError) as exc:
                logger.warning(
                    "Console Bee template pack dry-run failed for %s/%s",
                    pack.pack_id,
                    template.template_id,
                    exc_info=exc,
                )
                record_bee_pack_dry_run_metric(status="rejected")
                continue
            record_bee_pack_dry_run_metric(status=plan.status)
            summaries.append(
                ConsoleBeePackDryRunSummary(
                    pack_id=plan.pack_id,
                    template_id=plan.template_id,
                    source_type=plan.source.value,
                    status=safe_label_value(plan.status) or "unknown",
                    task_json_path=safe_text_value(plan.task_json_path) or "-",
                    report_path=safe_text_value(plan.report_path) or "-",
                    evidence_dir=safe_text_value(plan.evidence_dir) or "-",
                    memory_candidates_path=(
                        safe_text_value(plan.memory_candidates_path) or "-"
                    ),
                    node_count=len(plan.nodes),
                    command_count=len(plan.command_intents),
                    warning_count=len(plan.warnings),
                )
            )
    return tuple(sorted(summaries, key=lambda item: (item.pack_id, item.template_id)))


def _console_bee_workspace_template_summaries() -> tuple[
    ConsoleBeeTemplateSummary, ...
]:
    summaries = []
    for template in _console_bee_workspace_templates():
        intents = _safe_bee_workspace_command_intents(template)
        summaries.append(
            ConsoleBeeTemplateSummary(
                template_id=template.template_id,
                kind=safe_label_value(template.metadata.get("kind")) or "unknown",
                profile=safe_label_value(template.metadata.get("profile")) or "unknown",
                title=safe_label_value(template.metadata.get("title")) or "untitled",
                feature_count=len(template.feature_paths),
                has_commands=template.commands_path is not None,
                command_count=len(intents),
            )
        )
    return tuple(sorted(summaries, key=lambda item: item.template_id))


def _console_bee_workspace_run_artifact_summaries() -> tuple[
    ConsoleBeeRunArtifactSummary, ...
]:
    workspace_root = _console_bee_workspace_root()
    if workspace_root is None:
        return ()
    try:
        records = discover_bee_workspace_run_artifacts(workspace_root)
    except (
        OSError,
        ValueError,
        TypeError,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning(
            "Console Bee workspace run artifact discovery failed; rendering empty summaries",
            exc_info=exc,
        )
        return ()
    return tuple(_console_bee_run_artifact_summary(record) for record in records)


def _console_bee_run_artifact_summary(
    record: BeeWorkspaceRunArtifactRecord,
) -> ConsoleBeeRunArtifactSummary:
    return ConsoleBeeRunArtifactSummary(
        task_id=record.task_id,
        template_id=record.template_id,
        topic_id=record.topic_id,
        status=record.status,
        node_count=record.node_count,
        run_count=record.run_count,
        action_count=record.action_count,
        validation_count=record.validation_count,
        executor_count=record.executor_count,
        has_report=record.has_report,
        has_memory_candidates=record.has_memory_candidates,
    )


def _console_bee_workspace_command_summaries() -> tuple[
    ConsoleBeeCommandIntentSummary, ...
]:
    summaries = []
    for template in _console_bee_workspace_templates():
        for intent in _safe_bee_workspace_command_intents(template):
            summaries.append(_console_bee_command_summary(template.template_id, intent))
    return tuple(sorted(summaries, key=lambda item: (item.template_id, item.name)))


def _safe_bee_workspace_command_intents(
    template: BeeWorkspaceTemplate,
) -> tuple[BeeWorkspaceCommandIntent, ...]:
    try:
        return load_bee_workspace_command_intents(template)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(
            "Console Bee workspace command intent discovery failed for %s",
            template.template_id,
            exc_info=exc,
        )
        return ()


def _console_bee_command_summary(
    template_id: str,
    intent: BeeWorkspaceCommandIntent,
) -> ConsoleBeeCommandIntentSummary:
    return ConsoleBeeCommandIntentSummary(
        template_id=template_id,
        name=intent.name,
        profile=intent.profile,
        policy=intent.policy,
        category=intent.category,
        validation_label=intent.validation_label,
        status=intent.status,
    )


def _bee_node_summaries_from_run(
    run: AgentRunRecord,
) -> tuple[ConsoleBeeNodeSummary, ...]:
    metadata = run.metadata
    if metadata.get("bee_runtime") != "task_launch":
        return ()
    task_id = safe_id_value(metadata.get("task_id"))
    node_id = safe_id_value(metadata.get("node_id"))
    if task_id is None or node_id is None:
        return ()
    return (
        ConsoleBeeNodeSummary(
            task_id=task_id,
            node_id=node_id,
            run_id=safe_id_value(run.run_id),
            topic_id=safe_id_value(metadata.get("topic_id")),
            session_id=safe_id_value(metadata.get("session_id")),
            task_kind=safe_label_value(metadata.get("task_kind")) or "unknown",
            task_profile=safe_label_value(metadata.get("task_profile")) or "unknown",
            kind=safe_label_value(metadata.get("node_kind")) or "unknown",
            profile=safe_label_value(metadata.get("node_profile")) or "unknown",
            status=safe_label_value(run.status) or "unknown",
            context_profile=safe_label_value(metadata.get("context_profile")),
            validation_profile=safe_label_value(metadata.get("validation_profile")),
            workspace_policy=safe_label_value(metadata.get("workspace_policy")),
            approval_policy=safe_label_value(metadata.get("approval_policy")),
            action_policy=safe_label_value(metadata.get("action_policy")),
            workspace_binding=safe_label_value(metadata.get("workspace_binding")),
        ),
    )


def _combined_bee_status(current: str, next_status: str) -> str:
    if current == next_status:
        return current
    if "failed" in {current, next_status}:
        return "failed"
    if "running" in {current, next_status}:
        return "running"
    if "completed" in {current, next_status}:
        return "completed"
    return current


def _console_scheduled_run_store() -> PGScheduledRunStore | None:
    try:
        storage_config = _load_storage_config()
    except Exception:
        logger.exception(
            "Unable to load storage config for console scheduled run store"
        )
        return None
    if not _storage_uses_pg_http_sessions(storage_config):
        return None
    try:
        return PGScheduledRunStore(pool=session_manager.pg_pool)
    except Exception:
        logger.exception("Unable to initialize console scheduled run store")
        return None


def _console_topic_store() -> PGTopicStore | None:
    try:
        storage_config = _load_storage_config()
    except Exception:
        logger.exception("Unable to load storage config for console topic store")
        return None
    if not _storage_uses_pg_http_sessions(storage_config):
        return None
    try:
        return PGTopicStore(pool=session_manager.pg_pool)
    except Exception:
        logger.exception("Unable to initialize console topic store")
        return None


def _console_bee_launch_store() -> PGBeeLaunchStore | None:
    try:
        storage_config = _load_storage_config()
    except Exception:
        logger.exception("Unable to load storage config for console Bee launch store")
        return None
    if not _storage_uses_pg_http_sessions(storage_config):
        return None
    try:
        return PGBeeLaunchStore(pool=session_manager.pg_pool)
    except Exception:
        logger.exception("Unable to initialize console Bee launch store")
        return None


def _console_executor_run_store() -> PGExecutorRunStore | None:
    try:
        storage_config = _load_storage_config()
    except Exception:
        logger.exception("Unable to load storage config for console executor store")
        return None
    if not _storage_uses_pg_http_sessions(storage_config):
        return None
    try:
        return PGExecutorRunStore(pool=session_manager.pg_pool)
    except Exception:
        logger.exception("Unable to initialize console executor store")
        return None


async def _auth_context_can_access_topic(
    auth_context: AuthContext | None,
    topic: TopicRecord,
) -> bool:
    if auth_context is None or auth_context.scope == "admin":
        return True
    return topic.session_id in await _visible_console_session_ids(auth_context)


async def _visible_console_session_ids(auth_context: AuthContext | None) -> list[str]:
    session_ids: list[str] = []
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if _auth_context_can_access_session(auth_context, session):
            session_ids.append(session_id)
    return session_ids


def _topic_summary_from_record(
    topic: TopicRecord,
    cost: TopicCostRecord | None,
) -> ConsoleTopicSummary:
    return ConsoleTopicSummary(
        topic_id=safe_id_value(topic.topic_id) or "redacted",
        tape_id=safe_id_value(topic.tape_id),
        session_id=safe_id_value(topic.session_id),
        kind=safe_label_value(topic.kind) or "unknown",
        status=safe_label_value(topic.status) or "unknown",
        title=safe_text_value(topic.title),
        summary=safe_text_value(topic.summary),
        topic_initial_seq=topic.topic_initial_seq,
        topic_finalized_seq=topic.topic_finalized_seq,
        run_count=cost.run_count if cost is not None else 0,
        cost_total_tokens=cost.total_tokens if cost is not None else None,
    )


def _schedule_summary_from_record(schedule: ScheduleRecord) -> ConsoleScheduleSummary:
    return ConsoleScheduleSummary(
        schedule_id=safe_id_value(schedule.schedule_id) or "redacted",
        session_id=safe_id_value(schedule.session_id) or "redacted",
        topic_id=safe_id_value(schedule.topic_id),
        kind=safe_label_value(schedule.kind) or "unknown",
        status=safe_label_value(schedule.status) or "unknown",
        cadence=safe_label_value(schedule.cadence) or "unknown",
        title=safe_text_value(schedule.title),
        next_due_at=schedule.next_due_at,
        last_triggered_at=schedule.last_triggered_at,
    )


def _schedule_trigger_summary_from_record(
    trigger: ScheduleTriggerRecord,
) -> ConsoleScheduleTriggerSummary:
    return ConsoleScheduleTriggerSummary(
        trigger_id=safe_id_value(trigger.trigger_id) or "redacted",
        schedule_id=safe_id_value(trigger.schedule_id) or "redacted",
        signal_id=safe_id_value(trigger.signal_id),
        topic_id=safe_id_value(trigger.topic_id),
        run_id=safe_id_value(trigger.run_id),
        status=safe_label_value(trigger.status) or "unknown",
        due_at=trigger.due_at,
        planned_at=trigger.planned_at,
        reason=safe_label_value(trigger.reason),
    )


def _proactive_signal_summary_from_record(
    signal: ProactiveSignalRecord,
) -> ConsoleProactiveSignalSummary:
    return ConsoleProactiveSignalSummary(
        signal_id=safe_id_value(signal.signal_id) or "redacted",
        session_id=safe_id_value(signal.session_id),
        topic_id=safe_id_value(signal.topic_id),
        kind=safe_label_value(signal.kind) or "unknown",
        status=safe_label_value(signal.status) or "unknown",
        observed_at=signal.observed_at,
        cooldown_until=signal.cooldown_until,
        summary=safe_text_value(signal.summary),
    )


def _topic_anchor_summary_from_record(
    anchor: TopicAnchorRecord,
) -> ConsoleTopicAnchorSummary:
    return ConsoleTopicAnchorSummary(
        seq=anchor.seq,
        anchor_type=safe_label_value(anchor.anchor_type) or "unknown",
        entry_id=safe_id_value(anchor.entry_id),
    )


def _topic_recall_summary_from_record(
    recall: TopicRecallLinkRecord,
) -> ConsoleTopicRecallSummary:
    return ConsoleTopicRecallSummary(
        recalled_topic_id=safe_id_value(recall.recalled_topic_id) or "redacted",
        relation=safe_label_value(recall.relation) or "unknown",
        anchor_seq=recall.anchor_seq,
    )


def _topic_cost_summary_from_record(
    cost: TopicCostRecord | None,
) -> ConsoleTopicCostSummary | None:
    if cost is None:
        return None
    return ConsoleTopicCostSummary(
        prompt_tokens=cost.prompt_tokens,
        completion_tokens=cost.completion_tokens,
        total_tokens=cost.total_tokens,
        run_count=cost.run_count,
        action_count=cost.action_count,
        validation_count=cost.validation_count,
        tool_call_count=cost.tool_call_count,
    )


async def _visible_console_runs(
    auth_context: AuthContext | None,
) -> list[AgentRunRecord]:
    runs: list[AgentRunRecord] = []
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        try:
            runs.extend(await session_manager.list_runtime_runs(session_id))
        except RuntimeError:
            continue
    return runs


def _console_run_summary_from_run(run: AgentRunRecord) -> ConsoleRunSummary:
    return ConsoleRunSummary(
        run_id=run.run_id,
        session_id=run.session_id,
        status=run.status,
        started_at=run.started_at,
        ended_at=run.ended_at,
        error_summary=safe_error_summary(run.error),
    )


def _topic_summary_from_run(run: AgentRunRecord) -> ConsoleTopicSummary | None:
    topic = _topic_metadata(run)
    topic_id = _topic_id_from_run(run)
    if topic_id is None:
        return None
    cost = _safe_dict(topic.get("cost") or run.metadata.get("topic_cost"))
    return ConsoleTopicSummary(
        topic_id=topic_id,
        tape_id=safe_id_value(topic.get("tape_id") or run.tape_id),
        session_id=safe_id_value(topic.get("session_id") or run.session_id),
        kind=safe_label_value(topic.get("kind") or run.metadata.get("topic_kind"))
        or "unknown",
        status=safe_label_value(topic.get("status") or run.metadata.get("topic_status"))
        or "unknown",
        title=safe_text_value(topic.get("title")),
        summary=safe_text_value(topic.get("summary")),
        topic_initial_seq=_optional_int(
            _first_present(topic, run.metadata, "topic_initial_seq")
        ),
        topic_finalized_seq=_optional_int(
            _first_present(topic, run.metadata, "topic_finalized_seq")
        ),
        run_count=1,
        cost_total_tokens=_optional_int(cost.get("total_tokens")),
    )


def _topic_metadata(run: AgentRunRecord) -> dict[str, object]:
    return _safe_dict(run.metadata.get("topic"))


def _topic_id_from_run(run: AgentRunRecord) -> str | None:
    topic = _topic_metadata(run)
    return safe_id_value(topic.get("topic_id") or run.metadata.get("topic_id"))


def _first_present(
    primary: dict[str, object],
    secondary: dict[str, object],
    key: str,
) -> object:
    if key in primary:
        return primary[key]
    return secondary.get(key)


def _topic_anchor_summaries_from_run(
    run: AgentRunRecord,
) -> tuple[ConsoleTopicAnchorSummary, ...]:
    topic = _topic_metadata(run)
    anchors = _metadata_lists(
        {"items": topic.get("anchors") or run.metadata.get("topic_anchors")},
        ("items",),
    )
    summaries = []
    for anchor in anchors:
        anchor_type = safe_label_value(
            anchor.get("anchor_type") or anchor.get("product_anchor_type")
        )
        if anchor_type is None:
            continue
        summaries.append(
            ConsoleTopicAnchorSummary(
                seq=_optional_int(anchor.get("seq")),
                anchor_type=anchor_type,
                entry_id=safe_id_value(anchor.get("entry_id")),
            )
        )
    return tuple(summaries)


def _topic_recall_summaries_from_run(
    run: AgentRunRecord,
) -> tuple[ConsoleTopicRecallSummary, ...]:
    topic = _topic_metadata(run)
    recalls = _metadata_lists(
        {"items": topic.get("recall_links") or run.metadata.get("topic_recall_links")},
        ("items",),
    )
    summaries = []
    for recall in recalls:
        recalled_topic_id = safe_id_value(
            recall.get("recalled_topic_id") or recall.get("target_topic_id")
        )
        relation = safe_label_value(recall.get("relation")) or "unknown"
        if recalled_topic_id is None:
            continue
        summaries.append(
            ConsoleTopicRecallSummary(
                recalled_topic_id=recalled_topic_id,
                relation=relation,
                anchor_seq=_optional_int(recall.get("anchor_seq")),
            )
        )
    return tuple(summaries)


def _topic_cost_summary_from_run(run: AgentRunRecord) -> ConsoleTopicCostSummary | None:
    topic = _topic_metadata(run)
    cost = _safe_dict(topic.get("cost") or run.metadata.get("topic_cost"))
    if not cost:
        return None
    return ConsoleTopicCostSummary(
        prompt_tokens=_optional_int(cost.get("prompt_tokens")) or 0,
        completion_tokens=_optional_int(cost.get("completion_tokens")) or 0,
        total_tokens=_optional_int(cost.get("total_tokens")) or 0,
        run_count=_optional_int(cost.get("run_count")) or 0,
        action_count=_optional_int(cost.get("action_count")) or 0,
        validation_count=_optional_int(cost.get("validation_count")) or 0,
        tool_call_count=_optional_int(cost.get("tool_call_count")) or 0,
    )


def _observability_summary(
    *,
    correlation: ConsoleCorrelationSummary | None,
) -> ConsoleObservabilitySummary:
    config = _safe_observability_config()
    tracing_config = _safe_dict(config.get("tracing"))
    metrics_config = _safe_dict(config.get("metrics"))
    tracing_backend = safe_label_value(
        tracing_config.get("backend")
    ) or safe_label_value(config.get("backend"))
    metrics_backend = safe_label_value(metrics_config.get("backend") or "prometheus")
    return ConsoleObservabilitySummary(
        correlation=correlation,
        metrics_enabled=_prometheus_metrics_enabled(),
        metrics_path="/metrics",
        tracing_backend=tracing_backend,
        metrics_backend=metrics_backend,
        langfuse_url=_safe_observability_link(
            tracing_config.get("public_url")
            or tracing_config.get("ui_url")
            or config.get("langfuse_url")
        ),
        grafana_url=_safe_observability_link(
            metrics_config.get("grafana_url")
            or config.get("grafana_url")
            or config.get("dashboard_url")
        ),
    )


async def _console_workspace_summaries() -> list[ConsoleWorkspaceSummary]:
    if _remote_retention_enabled():
        records = await session_manager.list_workspace_records()
        summaries = [
            _console_workspace_summary_from_schema(
                _workspace_record_summary_response(record)
            )
            for record in records
        ]
    else:
        try:
            entries = await asyncio.to_thread(
                list_cloud_workspaces_from_config,
                _load_cloud_workspace_config(),
                active_workspace_ids=await _active_cloud_workspace_ids(),
            )
        except ValueError:
            summaries = []
        else:
            summaries = [
                _console_workspace_summary_from_schema(
                    _workspace_summary_response(entry)
                )
                for entry in entries
            ]
    summaries.sort(key=lambda item: item.updated_at, reverse=True)
    return summaries


def _console_workspace_capability_summary() -> ConsoleWorkspaceCapabilitySummary | None:
    try:
        capabilities = workspace_provider_capabilities_from_config(
            _load_cloud_workspace_config()
        )
    except ValueError:
        return None
    return _console_workspace_capability_from_provider(capabilities)


def _console_workspace_capability_from_provider(
    capabilities: WorkspaceProviderCapabilities,
) -> ConsoleWorkspaceCapabilitySummary:
    return ConsoleWorkspaceCapabilitySummary(
        provider=safe_label_value(capabilities.provider) or "redacted",
        available=capabilities.available,
        reason=safe_label_value(capabilities.reason) or "redacted",
        supports_provision=capabilities.supports_provision,
        supports_archive=capabilities.supports_archive,
        supports_diff=capabilities.supports_diff,
        supports_patch=capabilities.supports_patch,
        supports_publish=capabilities.supports_publish,
    )


def _console_workspace_summary_from_schema(
    workspace: WorkspaceSummarySchema,
) -> ConsoleWorkspaceSummary:
    result_refs = workspace.result_refs or {}
    return ConsoleWorkspaceSummary(
        workspace_id=safe_id_value(workspace.workspace_id) or "redacted",
        status=safe_label_value(workspace.status) or "redacted",
        updated_at=workspace.updated_at,
        session_id=safe_id_value(workspace.session_id),
        provider=safe_label_value(workspace.provider),
        provider_instance_id=safe_label_value(workspace.provider_instance_id),
        workspace_host_label=safe_label_value(workspace.workspace_host_label),
        source_kind=safe_label_value(workspace.source_kind),
        retention_policy=safe_label_value(workspace.retention_policy),
        expires_at=workspace.expires_at,
        is_local=workspace.is_local,
        result_ref_keys=safe_key_tuple(result_refs),
        cleanup_error=safe_error_summary(workspace.cleanup_error),
    )


async def _release_summary() -> ConsoleReleaseSummary:
    readiness_checks: dict[str, str]
    try:
        session_store_ok = bool(await session_manager.check_health_async())
    except Exception:
        logger.exception("Console session store readiness check failed")
        session_store_ok = False
    try:
        rate_limiter_ok = bool(limiter._storage.check())
    except Exception:
        logger.exception("Console rate limiter readiness check failed")
        rate_limiter_ok = False
    readiness_checks = {
        "session_store": "ok" if session_store_ok else "error",
        "rate_limiter": "ok" if rate_limiter_ok else "error",
    }
    ready = session_store_ok and rate_limiter_ok
    manifest_name = None
    gates: tuple[ConsoleReleaseGateSummary, ...] = ()
    manifest_path = Path("docs/release_hardening/release-verification.yaml")
    try:
        manifest = load_release_verification_manifest(manifest_path)
    except Exception:
        logger.exception("Unable to load release verification manifest")
    else:
        manifest_name = manifest.name
        gates = tuple(
            ConsoleReleaseGateSummary(
                gate_id=gate.id,
                command=gate.command,
                required=gate.required,
                scope=gate.scope,
            )
            for gate in manifest.gates
        )
    return ConsoleReleaseSummary(
        health_status="healthy",
        session_count=await session_manager.count_sessions_async(),
        version="2.0.0",
        readiness_status="ready" if ready else "not_ready",
        readiness_checks=tuple(sorted(readiness_checks.items())),
        release_manifest_name=manifest_name,
        release_gates=gates,
    )


def _action_summary_from_item(
    run_id: str,
    raw_item: dict[str, object],
) -> ConsoleActionSummary | None:
    kind = safe_label_value(raw_item.get("kind") or raw_item.get("action_kind"))
    status = safe_label_value(raw_item.get("status") or raw_item.get("action_status"))
    if kind is None or status is None:
        return None
    policy = _safe_dict(raw_item.get("policy"))
    patch_summary = _safe_dict(raw_item.get("patch_summary") or raw_item.get("patch"))
    return ConsoleActionSummary(
        action_id=safe_id_value(raw_item.get("action_id") or raw_item.get("id")),
        run_id=run_id,
        interaction_id=safe_id_value(
            raw_item.get("interaction_id") or raw_item.get("approval_interaction_id")
        ),
        validation_id=safe_id_value(raw_item.get("validation_id")),
        kind=kind,
        status=status,
        policy_decision=safe_label_value(
            raw_item.get("policy_decision") or policy.get("decision")
        ),
        risk_level=safe_label_value(raw_item.get("risk_level")),
        changed_path_count=_optional_int(raw_item.get("changed_path_count")),
        extension_buckets=_safe_label_tuple(raw_item.get("file_extension_buckets")),
        approval_status=safe_label_value(raw_item.get("approval_status")),
        patch_summary=_safe_summary_pairs(
            patch_summary,
            (
                "changed_path_count",
                "created_count",
                "updated_count",
                "deleted_count",
                "hunk_count",
                "risk_level",
            ),
        ),
    )


def _validation_outcomes(
    report: dict[str, object],
) -> list[ConsoleValidationOutcomeSummary]:
    raw_outcomes = report.get("outcomes")
    if not isinstance(raw_outcomes, list):
        return []
    outcomes: list[ConsoleValidationOutcomeSummary] = []
    for raw_outcome in raw_outcomes:
        if not isinstance(raw_outcome, dict):
            continue
        label = safe_label_value(raw_outcome.get("label")) or "redacted"
        status = safe_label_value(raw_outcome.get("status"))
        if status is None:
            continue
        policy = _safe_dict(raw_outcome.get("policy"))
        failure = _safe_dict(raw_outcome.get("failure_summary"))
        outcomes.append(
            ConsoleValidationOutcomeSummary(
                label=label,
                status=status,
                exit_code=_optional_int(raw_outcome.get("exit_code")),
                duration_ms=_optional_int(raw_outcome.get("duration_ms")),
                policy_decision=safe_label_value(policy.get("decision")),
                failure_summary=_safe_failure_summary_pairs(failure),
            )
        )
    return outcomes


def _metadata_lists(
    metadata: dict[str, object],
    keys: tuple[str, ...],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _first_metadata_item(
    metadata: dict[str, object],
    keys: tuple[str, ...],
) -> dict[str, object] | None:
    for item in _metadata_lists(metadata, keys):
        return item
    return None


def _safe_observability_config() -> dict[str, object]:
    try:
        return _load_observability_config()
    except Exception:
        logger.exception("Unable to load observability config for console")
        return {}


def _safe_observability_link(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"}:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    if not parts.netloc:
        return None
    return parts.geturl()


def _safe_label_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items: list[object] = [item.strip() for item in value.split(",")]
    elif isinstance(value, list | tuple):
        raw_items = list(value)
    else:
        return ()
    labels: list[str] = []
    for item in raw_items:
        label = safe_label_value(item)
        if label is not None:
            labels.append(label)
    return tuple(labels)


def _safe_summary_pairs(
    mapping: dict[str, object],
    allowed_keys: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for key in allowed_keys:
        value = mapping.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            pairs.append((key, str(value).lower()))
        elif isinstance(value, int | float | str):
            safe_value = safe_label_value(str(value))
            if safe_value is not None:
                pairs.append((key, safe_value))
    return tuple(pairs)


def _safe_failure_summary_pairs(
    mapping: dict[str, object],
) -> tuple[tuple[str, str], ...]:
    display_names = {
        "stdout_bytes": "output_bytes",
        "stderr_bytes": "error_bytes",
        "stdout_lines": "output_lines",
        "stderr_lines": "error_lines",
        "timeout_seconds": "timeout_seconds",
        "policy_decision": "policy_decision",
        "error_kind": "error_kind",
    }
    pairs: list[tuple[str, str]] = []
    for key, display_name in display_names.items():
        value = mapping.get(key)
        if isinstance(value, int | float):
            pairs.append((display_name, str(value)))
        elif isinstance(value, str):
            safe_value = safe_label_value(value)
            if safe_value is not None:
                pairs.append((display_name, safe_value))
    return tuple(pairs)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def _visible_console_tape_ids(
    auth_context: AuthContext | None,
) -> set[str]:
    if auth_context is None or auth_context.scope == "admin":
        return set()
    visible: set[str] = set()
    for session_id in await session_manager.list_sessions_async():
        try:
            session = await session_manager.get_session_async(session_id)
        except KeyError:
            continue
        if not _auth_context_can_access_session(auth_context, session):
            continue
        if session.tape_id is not None:
            visible.add(session.tape_id)
        try:
            runs = await session_manager.list_runtime_runs(session_id)
        except RuntimeError:
            runs = []
        for run in runs:
            if run.tape_id is not None:
                visible.add(run.tape_id)
    return visible


def _can_search_tape(
    *,
    auth_context: AuthContext | None,
    tape_id: str | None,
    run_id: str | None,
    visible_tape_ids: set[str],
) -> bool:
    if auth_context is None or auth_context.scope == "admin":
        return True
    if run_id is not None and tape_id is None:
        return False
    if tape_id is None:
        return True
    return tape_id in visible_tape_ids


async def _get_visible_runtime_run(
    run_id: str,
    auth_context: AuthContext | None,
) -> AgentRunRecord:
    try:
        record = await session_manager.load_runtime_run(run_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Runtime run not found") from exc

    try:
        await _get_visible_session(record.session_id, auth_context)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Runtime run not found",
            ) from exc
        raise
    return record


@app.get("/runs/{run_id}", response_model=RuntimeRunResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_run(
    request: Request,
    run_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeRunResponse:
    del request
    record = await _get_visible_runtime_run(run_id, auth_context)
    return _runtime_run_response(record)


@app.get(
    "/runs/{run_id}/interactions",
    response_model=RuntimeInteractionListResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_run_interactions(
    request: Request,
    run_id: str,
    status: str | None = Query(None, min_length=1, max_length=100),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionListResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        interactions = await session_manager.list_runtime_interactions(run_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime interactions not found",
        ) from exc
    if status is not None:
        interactions = [
            interaction for interaction in interactions if interaction.status == status
        ]
    return RuntimeInteractionListResponse(
        interactions=[
            _runtime_interaction_response(interaction) for interaction in interactions
        ]
    )


@app.get(
    "/runs/{run_id}/message-snapshot",
    response_model=RuntimeMessageSnapshotResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_message_snapshot(
    request: Request,
    run_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeMessageSnapshotResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        record = await session_manager.load_runtime_message_snapshot(run_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime message snapshot not found",
        ) from exc
    return _runtime_message_snapshot_response(record)


@app.get("/runs/{run_id}/events", response_model=RuntimeEventsResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_events(
    request: Request,
    run_id: str,
    last_event_id: str | None = Query(None, min_length=1, max_length=200),
    limit: int = Query(1000, ge=1, le=1000),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeEventsResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        events = await session_manager.replay_runtime_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime event not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail="Runtime run not found") from exc
    return RuntimeEventsResponse(
        run_id=run_id,
        events=[_runtime_event_response(event) for event in events],
    )


@app.get("/runs/{run_id}/display-events", response_model=DisplayEventsResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_display_events(
    request: Request,
    run_id: str,
    last_event_id: str | None = Query(None, min_length=1, max_length=200),
    limit: int = Query(1000, ge=1, le=1000),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> DisplayEventsResponse:
    del request
    _ = await _get_visible_runtime_run(run_id, auth_context)
    try:
        events = await session_manager.replay_display_events(
            run_id,
            last_event_id=last_event_id,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runtime event not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail="Runtime run not found") from exc
    return DisplayEventsResponse(
        run_id=run_id,
        events=[_display_event_response(event) for event in events],
    )


@app.get("/interactions", response_model=RuntimeInteractionListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_runtime_interactions(
    request: Request,
    session_id: str | None = Query(None, min_length=1, max_length=100),
    run_id: str | None = Query(None, min_length=1, max_length=100),
    status: str | None = Query(None, min_length=1, max_length=100),
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionListResponse:
    del request
    if run_id is not None:
        runs = [await _get_visible_runtime_run(run_id, auth_context)]
    elif session_id is not None:
        _ = await _get_visible_session(session_id, auth_context)
        runs = await session_manager.list_runtime_runs(session_id)
    else:
        runs = await _visible_runtime_runs(auth_context)
    interactions: list[AgentInteractionRecord] = []
    for run in runs:
        if session_id is not None and run.session_id != session_id:
            continue
        try:
            interactions.extend(
                await session_manager.list_runtime_interactions(run.run_id)
            )
        except RuntimeError:
            continue
    if status is not None:
        interactions = [
            interaction for interaction in interactions if interaction.status == status
        ]
    interactions.sort(key=lambda interaction: interaction.created_at, reverse=True)
    return RuntimeInteractionListResponse(
        interactions=[
            _runtime_interaction_response(interaction) for interaction in interactions
        ]
    )


@app.get(
    "/interactions/{interaction_id}",
    response_model=RuntimeInteractionResponse,
)
@limiter.limit(RateLimits.GET_SESSION)
async def get_runtime_interaction(
    request: Request,
    interaction_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionResponse:
    del request
    try:
        interaction = await session_manager.load_runtime_interaction(interaction_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime interaction not found",
        ) from exc
    _ = await _get_visible_runtime_run(interaction.run_id, auth_context)
    return _runtime_interaction_response(interaction)


@app.post(
    "/interactions/{interaction_id}/resolve",
    response_model=RuntimeInteractionResponse,
)
@limiter.limit(RateLimits.APPROVE)
async def resolve_runtime_interaction(
    request: Request,
    interaction_id: str,
    body: ResolveInteractionRequest,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> RuntimeInteractionResponse:
    del request
    try:
        interaction = await session_manager.load_runtime_interaction(interaction_id)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Runtime interaction not found",
        ) from exc
    _ = await _get_visible_runtime_run(interaction.run_id, auth_context)
    if interaction.interaction_kind != "approval":
        raise HTTPException(status_code=400, detail="Interaction is not an approval")
    if interaction.status != "pending":
        raise HTTPException(status_code=409, detail="Interaction is not pending")
    session_id = interaction.metadata.get("session_id")
    request_id = interaction.metadata.get("request_id")
    if not isinstance(session_id, str) or not isinstance(request_id, str):
        raise HTTPException(
            status_code=400,
            detail="Approval interaction metadata is incomplete",
        )
    try:
        approval = await session_manager.submit_approval_response(
            session_id=session_id,
            request_id=request_id,
            approved=body.approved,
            feedback=body.feedback,
            scope=body.scope,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    if approval is None:
        raise HTTPException(
            status_code=409,
            detail="Approval request is no longer pending",
        )
    resolved = await session_manager.load_runtime_interaction(interaction_id)
    return _runtime_interaction_response(resolved)


@app.get("/workers", response_model=WorkerListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_workers(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkerListResponse:
    del request
    return WorkerListResponse(
        workers=_worker_statuses_from_runs(await _visible_runtime_runs(auth_context))
    )


@app.get("/executors", response_model=ExecutorListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_executors(
    request: Request,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> ExecutorListResponse:
    del request
    return ExecutorListResponse(
        executors=_worker_statuses_from_runs(await _visible_runtime_runs(auth_context))
    )


@app.get("/workers/{worker_id}", response_model=WorkerStatusResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_worker_status(
    request: Request,
    worker_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkerStatusResponse:
    del request
    try:
        return _worker_status_from_runs(
            worker_id,
            await _visible_runtime_runs(auth_context),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Worker not found") from exc


@app.get("/executors/{executor_id}", response_model=WorkerStatusResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_executor_status(
    request: Request,
    executor_id: str,
    auth_context: AuthContext | None = Depends(auth_context_from_headers),
) -> WorkerStatusResponse:
    del request
    try:
        return _worker_status_from_runs(
            executor_id,
            await _visible_runtime_runs(auth_context),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Executor not found") from exc


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


def _runtime_event_message(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    message = payload.get("message")
    if isinstance(message, Mapping):
        return cast(Mapping[str, Any], message)
    return payload


def _runtime_event_string(message: Mapping[str, Any], key: str) -> str:
    value = message.get(key)
    return value if isinstance(value, str) else ""


def _runtime_event_arguments(message: Mapping[str, Any]) -> JSONObject:
    arguments = message.get("arguments")
    if isinstance(arguments, Mapping):
        return cast(JSONObject, dict(arguments))
    return {}


async def _session_result_runtime_run_id(session: Session) -> str | None:
    if session.current_turn_id is not None:
        return session.current_turn_id
    try:
        runs = await session_manager.list_runtime_runs(session.id)
    except RuntimeError:
        return None
    if not runs:
        return None
    latest = max(
        runs,
        key=lambda run: run.ended_at or run.started_at,
    )
    return latest.run_id


async def _session_result_from_runtime_events(run_id: str) -> TurnResult | None:
    _ = await session_manager.load_runtime_run(run_id)
    events = await session_manager.replay_runtime_events(run_id, limit=1000)

    content_parts: list[str] = []
    tool_calls: dict[str, ToolCallRecord] = {}
    anonymous_tool_calls: list[ToolCallRecord] = []
    for event in events:
        message = _runtime_event_message(event.payload)
        if event.event_kind == "wire.StreamDelta":
            if _runtime_event_string(message, "agent_id"):
                continue
            content = _runtime_event_string(message, "content")
            if content:
                content_parts.append(content)
        elif event.event_kind == "wire.ToolCallDelta":
            record = ToolCallRecord(
                call_id=_runtime_event_string(message, "call_id"),
                name=_runtime_event_string(message, "tool_name"),
                arguments=_runtime_event_arguments(message),
            )
            if record.call_id:
                tool_calls[record.call_id] = record
            else:
                anonymous_tool_calls.append(record)

    final_output = "".join(content_parts) if content_parts else None
    if final_output is None and not tool_calls and not anonymous_tool_calls:
        return None
    return result_from_turn_trace(
        TurnTrace(
            user_input="",
            tool_calls=tuple([*tool_calls.values(), *anonymous_tool_calls]),
            final_output=final_output,
        )
    )


def _session_result_failure_details(session: Session) -> str | None:
    details = session.last_failure_details
    if details is not None:
        return details
    if session.turn_status == "failed":
        return "Session turn failed; no failure details were recorded."
    return None


def _session_local_workspace_root(session: Session) -> Path | None:
    target = session.default_run_target
    if target is None:
        raise RuntimeError("session is missing default_run_target")
    workspace = target.workspace
    if not isinstance(workspace, LocalPathWorkspaceRef):
        return None
    root = Path(workspace.path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"local workspace does not exist: {root}")
    if not (root / ".git").exists():
        raise ValueError("local workspace diff requires a Git workspace")
    return root


def _run_local_workspace_git(
    workspace_root: Path,
    args: list[str],
    operation: str,
    *,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    git_binary = shutil.which("git")
    if git_binary is None:
        raise ValueError("git executable not found")
    try:
        result = subprocess.run(
            [git_binary, *args],
            cwd=workspace_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                **(extra_env or {}),
            },
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        stdout = exc.stdout.strip()
        detail = stderr or stdout or f"exit code {exc.returncode}"
        raise ValueError(f"{operation} failed: {detail}") from exc
    return result.stdout


def _parse_local_git_name_status(
    output: str,
    numstat: Mapping[str, tuple[int | None, int | None, bool]],
) -> list[WorkspaceDiffFile]:
    tokens = [token for token in output.split("\0") if token]
    files: list[WorkspaceDiffFile] = []
    index = 0
    while index < len(tokens):
        status_and_path = tokens[index]
        index += 1
        status_parts = status_and_path.split("\t", 1)
        if len(status_parts) == 2:
            status_token, first_path = status_parts
        else:
            status_token = status_and_path
            if index >= len(tokens):
                raise ValueError("git diff name-status output is malformed")
            first_path = tokens[index]
            index += 1
        status_code = status_token[:1]
        old_path: str | None = None
        if status_code in {"R", "C"}:
            if index >= len(tokens):
                raise ValueError("git diff name-status output is malformed")
            old_path = first_path
            path = tokens[index]
            index += 1
            status: Literal[
                "added", "modified", "deleted", "renamed", "binary", "unknown"
            ] = "renamed"
        else:
            path = first_path
            status = _local_workspace_diff_status_from_git_status(status_code)

        additions, deletions, binary = numstat.get(path, (None, None, False))
        files.append(
            WorkspaceDiffFile(
                path=path,
                status=status,
                old_path=old_path,
                additions=additions,
                deletions=deletions,
                binary=binary,
            )
        )
    return files


def _local_workspace_diff_status_from_git_status(
    status_code: str,
) -> Literal["added", "modified", "deleted", "renamed", "binary", "unknown"]:
    if status_code == "A":
        return "added"
    if status_code == "M":
        return "modified"
    if status_code == "D":
        return "deleted"
    return "unknown"


def _parse_local_git_numstat(
    output: str,
) -> dict[str, tuple[int | None, int | None, bool]]:
    result: dict[str, tuple[int | None, int | None, bool]] = {}
    tokens = output.split("\0")
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if not record:
            continue
        parts = record.split("\t", 2)
        if len(parts) != 3:
            raise ValueError("git diff numstat output is malformed")
        raw_additions, raw_deletions, raw_path = parts
        if raw_path:
            path = raw_path
        else:
            if index + 1 >= len(tokens):
                raise ValueError("git diff numstat output is malformed")
            _old_path = tokens[index]
            path = tokens[index + 1]
            index += 2
        if raw_additions == "-" or raw_deletions == "-":
            result[path] = (None, None, True)
            continue
        result[path] = (int(raw_additions), int(raw_deletions), False)
    return result


def _local_workspace_untracked_paths(workspace_root: Path) -> list[str]:
    output = _run_local_workspace_git(
        workspace_root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        "git ls-files --others",
    )
    return [path for path in output.split("\0") if path]


def _local_workspace_untracked_file(
    workspace_root: Path,
    relative_path: str,
) -> WorkspaceDiffFile:
    path = (workspace_root / relative_path).resolve()
    try:
        _ = path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"untracked path escapes workspace: {relative_path}") from exc
    if not path.is_file():
        return WorkspaceDiffFile(path=relative_path, status="unknown", binary=False)
    data = path.read_bytes()
    if b"\0" in data:
        return WorkspaceDiffFile(
            path=relative_path,
            status="added",
            additions=None,
            deletions=None,
            binary=True,
        )
    text = data.decode("utf-8", errors="replace")
    additions = 0 if text == "" else len(text.splitlines())
    return WorkspaceDiffFile(
        path=relative_path,
        status="added",
        additions=additions,
        deletions=0,
        binary=False,
    )


def _local_workspace_untracked_patch(workspace_root: Path, relative_path: str) -> str:
    path = (workspace_root / relative_path).resolve()
    try:
        _ = path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"untracked path escapes workspace: {relative_path}") from exc
    if not path.is_file():
        return ""
    data = path.read_bytes()
    if b"\0" in data:
        return f"diff --git a/{relative_path} b/{relative_path}\nnew file mode 100644\nBinary files /dev/null and b/{relative_path} differ\n"
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    patch_lines = [
        f"diff --git a/{relative_path} b/{relative_path}\n",
        "new file mode 100644\n",
        "index 0000000..0000000\n",
        "--- /dev/null\n",
        f"+++ b/{relative_path}\n",
        f"@@ -0,0 +1,{len(lines)} @@\n",
    ]
    patch_lines.extend(f"+{line}" for line in lines)
    if lines and not lines[-1].endswith("\n"):
        patch_lines.append("\n\\ No newline at end of file\n")
    return "".join(patch_lines)


def _local_workspace_diff(workspace_root: Path) -> WorkspaceDiff:
    name_status_output = _run_local_workspace_git(
        workspace_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--find-renames",
            "-z",
            "HEAD",
            "--",
        ],
        "git diff --name-status",
    )
    numstat_output = _run_local_workspace_git(
        workspace_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--numstat",
            "--find-renames",
            "-z",
            "HEAD",
            "--",
        ],
        "git diff --numstat",
    )
    numstat = _parse_local_git_numstat(numstat_output)
    files = _parse_local_git_name_status(name_status_output, numstat)
    for untracked_path in _local_workspace_untracked_paths(workspace_root):
        file = _local_workspace_untracked_file(workspace_root, untracked_path)
        files.append(file)
        numstat[file.path] = (file.additions, file.deletions, file.binary)
    additions = sum(item[0] for item in numstat.values() if item[0] is not None)
    deletions = sum(item[1] for item in numstat.values() if item[1] is not None)
    return WorkspaceDiff(
        workspace_id=str(workspace_root),
        files=files,
        additions=additions,
        deletions=deletions,
    )


def _local_workspace_patch(workspace_root: Path) -> WorkspacePatch:
    patch = _run_local_workspace_git(
        workspace_root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "HEAD",
            "--",
        ],
        "git diff",
    )
    untracked_patch = "".join(
        _local_workspace_untracked_patch(workspace_root, path)
        for path in _local_workspace_untracked_paths(workspace_root)
    )
    return WorkspacePatch(
        workspace_id=str(workspace_root),
        format="unified_diff",
        patch=patch + untracked_patch,
    )


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
    result_turn_id = session.current_turn_id
    if latest_turn is None:
        result_turn_id = await _session_result_runtime_run_id(session)
        turn_result = (
            None
            if result_turn_id is None
            else await _session_result_from_runtime_events(result_turn_id)
        )
    else:
        turn_result = result_from_turn_trace(latest_turn)
    verification_summary = (
        None
        if turn_result is None or turn_result.verification_summary is None
        else turn_result.verification_summary.summary
    )
    return SessionResultResponse(
        session_id=session.id,
        status=summary["status"],
        turn_status=summary["turn_status"],
        turn_id=result_turn_id,
        workspace_id=summary["workspace_id"],
        origin=session.origin,
        provider_name=session.provider_name,
        model_name=session.model_name,
        final_answer=None if turn_result is None else turn_result.final_output,
        verification_summary=verification_summary,
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
    session = await _get_visible_session(session_id, auth_context)
    try:
        local_workspace_root = _session_local_workspace_root(session)
        if local_workspace_root is not None:
            diff = await asyncio.to_thread(_local_workspace_diff, local_workspace_root)
        else:
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
    session = await _get_visible_session(session_id, auth_context)
    try:
        local_workspace_root = _session_local_workspace_root(session)
        if local_workspace_root is not None:
            patch = await asyncio.to_thread(
                _local_workspace_patch, local_workspace_root
            )
        else:
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

    await _persist_workspace_publication_refs(
        session_id,
        publication=publication,
        mode="branch",
        pr_url=None,
    )
    return PublishSessionResponse(
        session_id=session_id,
        mode="branch",
        status=publication.status,
        branch_name=publication.branch_name,
        pushed_ref=publication.pushed_ref,
        commit_sha=publication.commit_sha,
        remote_url=publication.remote_url,
        pr_url=None,
        error=publication.error,
    )


async def _publish_session_pr_response(
    session_id: str,
    publication: WorkspaceBranchPublication,
    publication_config: dict[str, Any],
) -> PublishSessionResponse:
    if publication.status == "partial":
        await _persist_workspace_publication_refs(
            session_id,
            publication=publication,
            mode="branch",
            pr_url=None,
        )
        return PublishSessionResponse(
            session_id=session_id,
            mode="pr",
            status="partial",
            branch_name=publication.branch_name,
            pushed_ref=publication.pushed_ref,
            commit_sha=publication.commit_sha,
            remote_url=publication.remote_url,
            pr_url=None,
            error=publication.error,
        )
    try:
        pr_url = await _create_github_pull_request(
            session_id,
            publication,
            publication_config,
        )
    except GitHubPrUnsupportedError as exc:
        await _persist_workspace_publication_refs(
            session_id,
            publication=publication,
            mode="branch",
            pr_url=None,
        )
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
        await _persist_workspace_publication_refs(
            session_id,
            publication=publication,
            mode="branch",
            pr_url=None,
        )
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
    await _persist_workspace_publication_refs(
        session_id,
        publication=publication,
        mode="pr",
        pr_url=pr_url,
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
