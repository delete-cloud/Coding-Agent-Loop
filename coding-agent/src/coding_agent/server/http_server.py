"""FastAPI-based HTTP server for Coding Agent with REST endpoints and SSE streaming."""

from __future__ import annotations

import asyncio
from importlib import import_module

import httpx
from agentkit.result.reducers import result_from_turn_trace
from sse_starlette.sse import EventSourceResponse

from coding_agent.server.schemas import PromptRequest

from coding_agent.environment import (
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
from coding_agent.runs import UNSET
from coding_agent.server.provider_models import list_provider_models
from coding_agent.server.http.constants import (
    APPROVAL_TIMEOUT_SECONDS,
    SESSION_IDLE_TIMEOUT_MINUTES,
    WORKER_OFFLINE_AFTER_SECONDS,
    WORKER_STALE_AFTER_SECONDS,
    _CORS_ORIGINS_ENV,
    _SERVER_CONFIG_ENV,
    _WEBUI_DIST_DIR_ENV,
)
from coding_agent.server.oauth_flows import CodexOAuthFlowManager
from coding_agent.server.rate_limit import limiter
from coding_agent.server.stores.session_owner_store import SessionOwnerStore

_IMPL_MODULES = (
    "coding_agent.server.http.config",
    "coding_agent.server.http.deps",
    "coding_agent.server.http.workspace_retention",
    "coding_agent.server.http.session_target",
    "coding_agent.server.http.events",
    "coding_agent.server.http.local_git",
    "coding_agent.server.http.lifecycle",
    "coding_agent.server.http.memory_review",
    "coding_agent.server.http.console_summaries",
    "coding_agent.server.http.console_topics",
    "coding_agent.server.http.console_bee",
    "coding_agent.server.http.console_bee_packs",
    "coding_agent.server.http.console_stores",
    "coding_agent.server.http.console_run_meta",
    "coding_agent.server.http.console_actions",
    "coding_agent.server.http.routes.health",
    "coding_agent.server.http.routes.providers",
    "coding_agent.server.http.routes.sessions",
    "coding_agent.server.http.routes.memory",
    "coding_agent.server.http.routes.prompts",
    "coding_agent.server.http.routes.sse",
    "coding_agent.server.http.routes.workers",
    "coding_agent.server.http.routes.workspaces",
    "coding_agent.server.http.routes.runtime",
    "coding_agent.server.http.routes.console",
    "coding_agent.server.http.routes.console_ops",
    "coding_agent.server.http.routes.session_result",
    "coding_agent.server.http.routes.session_workspace",
    "coding_agent.server.http.routes.publish",
    "coding_agent.server.http.routes.checkpoints",
    "coding_agent.server.http.routes.oauth",
)

for _mod_name in _IMPL_MODULES:
    _mod = import_module(_mod_name)
    for _name in _mod.__all__:
        globals()[_name] = getattr(_mod, _name)

_build_session_manager = globals()["_build_session_manager"]
session_manager = _build_session_manager()
codex_oauth_flow_manager = CodexOAuthFlowManager()

from coding_agent.server.http.app import (  # noqa: E402
    _HTTPMetricsASGIMiddleware,
    create_app,
    rate_limit_handler,
)

app = create_app()

__all__ = [
    "APPROVAL_TIMEOUT_SECONDS",
    "EventSourceResponse",
    "PromptRequest",
    "SESSION_IDLE_TIMEOUT_MINUTES",
    "SessionOwnerStore",
    "UNSET",
    "WORKER_OFFLINE_AFTER_SECONDS",
    "WORKER_STALE_AFTER_SECONDS",
    "_CORS_ORIGINS_ENV",
    "_HTTPMetricsASGIMiddleware",
    "_SERVER_CONFIG_ENV",
    "_WEBUI_DIST_DIR_ENV",
    "app",
    "asyncio",
    "cleanup_cloud_binding_from_config",
    "cleanup_cloud_workspace_from_config",
    "cleanup_stale_cloud_workspaces_from_config",
    "cloud_client_factory_from_config",
    "cloud_workspace_ready_from_config",
    "codex_oauth_flow_manager",
    "create_app",
    "export_workspace_archive_by_id_from_config",
    "export_workspace_archive_from_config",
    "get_cloud_workspace_from_config",
    "httpx",
    "list_cloud_workspaces_from_config",
    "limiter",
    "list_provider_models",
    "provision_cloud_binding_from_config",
    "result_from_turn_trace",
    "publish_workspace_branch_from_config",
    "rate_limit_handler",
    "session_manager",
    "workspace_archive_manifest_from_config",
    "workspace_diff_from_config",
    "workspace_patch_from_config",
    "workspace_provider_capabilities_from_config",
]
