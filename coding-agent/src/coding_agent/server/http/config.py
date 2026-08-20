"""Server config loading, CORS, static mount, and production validation."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agentkit.config import loader as agent_config_loader
from agentkit.errors import ConfigError

from coding_agent.server.http import _bindings
from coding_agent.server.http.constants import (
    _CORS_ORIGINS_ENV,
    _SERVER_CONFIG_ENV,
    _WEBUI_DIST_DIR_ENV,
)
from coding_agent.server.http._bindings import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


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


def _server_config_path() -> Path:
    configured_path = os.environ.get(_SERVER_CONFIG_ENV)
    if configured_path is not None and configured_path.strip():
        return Path(configured_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "agent.toml"


def _has_explicit_server_config() -> bool:
    configured_path = os.environ.get(_SERVER_CONFIG_ENV)
    return configured_path is not None and bool(configured_path.strip())


def _cors_allowed_origins(environ: Mapping[str, str] = os.environ) -> list[str]:
    raw_origins = environ.get(_CORS_ORIGINS_ENV)
    if raw_origins is None:
        return ["*"]
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if not origins:
        raise ValueError(f"{_CORS_ORIGINS_ENV} must contain at least one origin")
    return origins


def mount_webui_static_files(app: FastAPI, dist_dir: str | None = None) -> None:
    configured_dir = (
        os.environ.get(_WEBUI_DIST_DIR_ENV) if dist_dir is None else dist_dir
    )
    if configured_dir is None or not configured_dir.strip():
        return
    dist_path = Path(configured_dir).expanduser()
    if not dist_path.is_dir():
        raise RuntimeError(f"{_WEBUI_DIST_DIR_ENV} must point to an existing directory")

    # Registered last so API and /console routes keep precedence. StaticFiles
    # serves "/" but does not implement SPA deep-link fallback.
    app.mount("/", StaticFiles(directory=str(dist_path), html=True), name="webui")


def _load_agent_config_section(section: str) -> dict[str, Any]:
    config_path = _server_config_path()
    try:
        return cast(
            dict[str, Any],
            agent_config_loader.load_config(config_path).extra.get(section, {}),
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
    runtime_profiles = _bindings.module()._load_runtime_profiles_config()
    if runtime_profiles:
        cloud_workspace_config = dict(cloud_workspace_config)
        cloud_workspace_config["runtime_profiles"] = runtime_profiles
    remote_sources = _bindings.module()._load_remote_sources_config()
    if remote_sources:
        cloud_workspace_config = dict(cloud_workspace_config)
        cloud_workspace_config["remote_sources"] = remote_sources
    remote_phases = _bindings.module()._load_remote_phases_config()
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
        config = _bindings.module()._load_observability_config()
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
        config = agent_config_loader.load_config(config_path)
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


__all__ = [
    "_storage_uses_pg_http_sessions",
    "_config_int_or_default",
    "_cors_allowed_origins",
    "_has_explicit_server_config",
    "_is_root_exec_user",
    "_load_agent_config_section",
    "_load_agent_runtime_defaults",
    "_load_bee_workspace_config",
    "_load_cloud_workspace_config",
    "_load_observability_config",
    "_load_remote_phases_config",
    "_load_remote_publication_config",
    "_load_remote_retention_config",
    "_load_remote_sources_config",
    "_load_runtime_profiles_config",
    "_load_server_config",
    "_load_storage_config",
    "_log_development_mode_warning",
    "_optional_config_string",
    "_prometheus_metrics_enabled",
    "_require_non_empty_string",
    "_require_positive_int",
    "_require_positive_int_field",
    "_require_string_list_field",
    "_server_config_path",
    "_validate_production_config",
    "_validate_production_remote_phases",
    "_validate_production_remote_retention",
    "mount_webui_static_files",
]
