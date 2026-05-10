"""Tests for HTTP API server."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys
import types
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from typing import cast
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport
from httpx_sse import aconnect_sse
from starlette.requests import Request
from agentkit.errors import ConfigError
from agentkit.checkpoint.models import CheckpointMeta
from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.tape.tape import Tape
from agentkit.tools import FatalToolExecutionError

from coding_agent.approval import ApprovalPolicy
from coding_agent.approval.store import ApprovalStore
from coding_agent.core.config import settings
from coding_agent.environment import CloudCommandResult, CloudEnvironment
from coding_agent.ui.execution_binding import (
    CloudWorkspaceBinding,
    LocalExecutionBinding,
)
from coding_agent.wire.local import LocalWire
from coding_agent.ui.session_manager import Session
from coding_agent.ui.session_owner_store import SessionOwnerRecord
from coding_agent.ui.session_owner_store import SessionOwnershipConflictError
from coding_agent.ui.http_server import (
    SESSION_IDLE_TIMEOUT_MINUTES,
    _build_binding_resolver,
    _build_session_manager,
    _renew_owner_leases,
    _cleanup_event_queue_on_disconnect,
    _broadcast_event,
    get_events,
    _session_to_dict,
    stream_wire_messages,
    _wire_message_to_event,
    app,
    limiter,
    session_manager,
    wait_for_approval,
)
import coding_agent.ui.http_server as http_server
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    ThinkingDelta,
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnStatusDelta,
    TurnEnd,
)


@pytest.fixture(autouse=True)
async def clear_sessions():
    """Clear sessions before each test."""
    session_manager.configure_owner_leases(
        owner_store=None,
        owner_id=None,
        fencing_token=None,
    )
    session_manager.clear_sessions()
    # Clear rate limit storage to prevent 429 errors
    limiter.reset()
    # Also close any sessions in session_manager
    for session_id in list(session_manager.list_sessions()):
        try:
            await session_manager.close_session(session_id)
        except Exception:
            pass
    yield
    session_manager.configure_owner_leases(
        owner_store=None,
        owner_id=None,
        fencing_token=None,
    )
    session_manager.clear_sessions()
    # Cleanup session_manager
    for session_id in list(session_manager.list_sessions()):
        try:
            await session_manager.close_session(session_id)
        except Exception:
            pass


def register_session(
    session_id: str,
    **overrides,
) -> Session:
    session = Session(
        id=session_id,
        created_at=overrides.pop("created_at", datetime.now()),
        last_activity=overrides.pop("last_activity", datetime.now()),
        **overrides,
    )
    session_manager.register_session(session)
    return session


def _minimal_agent_toml(extra: str = "") -> str:
    return (
        "[agent]\n"
        'name = "test-agent"\n'
        'model = "test-model"\n'
        'provider = "openai"\n'
        f"{extra}"
    )


def _safe_production_cloud_workspace_config() -> dict[str, object]:
    return {
        "enabled": True,
        "provider": "docker",
        "workspace_root": "/srv/coding-agent/workspaces",
        "image": "coding-agent-runtime:2026-05-10",
        "image_allowlist": ["coding-agent-runtime:2026-05-10"],
        "exec_user": "1000:1000",
        "max_active_workspaces": 8,
        "max_workspace_age_seconds": 86400,
        "gc_interval_seconds": 300,
        "network": "none",
        "cpus": "2",
        "memory": "4g",
        "pids_limit": 512,
    }


def _write_auth_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        _minimal_agent_toml(
            """
[server]
bearer_token = "user-token-a"
admin_bearer_token = "admin-token"
"""
        ),
        encoding="utf-8",
    )
    return config_path


def test_http_server_loads_config_from_explicit_server_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        _minimal_agent_toml(
            """
[server]
production = false

[cloud_workspace]
enabled = true
provider = "docker"
workspace_root = "/srv/coding-agent/workspaces"
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

    assert http_server._server_config_path() == config_path
    assert http_server._load_server_config() == {"production": False}
    assert http_server._load_cloud_workspace_config()["workspace_root"] == (
        "/srv/coding-agent/workspaces"
    )


def test_http_server_explicit_server_config_missing_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_config_path = tmp_path / "missing.toml"
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(missing_config_path))

    with pytest.raises(ConfigError, match="config file not found"):
        _ = http_server._load_server_config()


def test_production_config_accepts_safe_docker_workspace_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_BEARER_TOKEN", "secret-token")

    http_server._validate_production_config(
        {
            "production": True,
            "bearer_token_env": "CODING_AGENT_BEARER_TOKEN",
        },
        _safe_production_cloud_workspace_config(),
    )


@pytest.mark.parametrize(
    ("server_config", "cloud_workspace_overrides", "message"),
    [
        (
            {"production": True},
            {},
            "server.bearer_token_env",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"enabled": False},
            "cloud_workspace.enabled=true",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"image_allowlist": []},
            "cloud_workspace.image_allowlist",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"exec_user": "0:1000"},
            "cloud_workspace.exec_user must not be root",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"max_active_workspaces": 0},
            "cloud_workspace.max_active_workspaces",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"max_active_workspaces": True},
            "cloud_workspace.max_active_workspaces",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"max_workspace_age_seconds": 0},
            "cloud_workspace.max_workspace_age_seconds",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"gc_interval_seconds": 0},
            "cloud_workspace.gc_interval_seconds",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"cpus": ""},
            "cloud_workspace.cpus",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"memory": ""},
            "cloud_workspace.memory",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"pids_limit": 0},
            "cloud_workspace.pids_limit",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"network": "bridge"},
            'cloud_workspace.network must be "none"',
        ),
    ],
)
def test_production_config_rejects_unsafe_remote_workspace_config(
    server_config: dict[str, object],
    cloud_workspace_overrides: dict[str, object],
    message: str,
) -> None:
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config.update(cloud_workspace_overrides)

    with pytest.raises(ValueError, match=re.escape(message)):
        http_server._validate_production_config(
            server_config,
            cloud_workspace_config,
        )


def test_development_mode_warning_logs_when_production_is_not_enabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    http_server._log_development_mode_warning({"production": False})

    assert "not safe for team production use" in caplog.text


def add_store_backed_approval_request(
    session: Session,
    session_id: str,
    request_id: str,
) -> None:
    tool_call = ToolCallDelta(
        session_id=session_id,
        tool_name="bash",
        arguments={"command": "ls"},
        call_id=f"call-{request_id}",
    )
    approval_req = ApprovalRequest(
        session_id=session_id,
        request_id=request_id,
        tool_call=tool_call,
        timeout_seconds=120,
    )
    session.approval_store.add_request(approval_req)


class FakeCloudClient:
    workspace_id = "ws-configured"
    workspace_url = "https://workspace.example.com"
    default_cwd = "/workspace"

    def read_file(self, path: str) -> str:
        return path

    def write_file(self, path: str, content: str) -> None:
        del path, content

    def replace_file(self, path: str, old: str, new: str) -> None:
        del path, old, new

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return []

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del pattern, directory, include
        return []

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        del patch
        return {"success": True, "path": path, "changed": False}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        del command, cwd, env, timeout
        return CloudCommandResult(stdout="", stderr="", exit_code=0)


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestSessionCreation:
    """Tests for session creation endpoint."""

    async def test_create_session(self, client):
        """Test creating a new session."""
        response = await client.post("/sessions", json={})
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36  # UUID format

    async def test_create_session_stores_in_memory(self, client):
        """Test that created session is stored in memory."""
        response = await client.post("/sessions", json={})
        data = response.json()
        session_id = data["session_id"]
        assert session_manager.has_session(session_id)
        assert session_manager.get_session(session_id).id == session_id

    async def test_healthz_reports_store_backed_session_count(self, client):
        response = await client.post("/sessions", json={})
        session_id = response.json()["session_id"]

        health = await client.get("/healthz")

        assert health.status_code == 200
        assert health.json()["sessions"] == 1
        assert session_manager.has_session(session_id)

    async def test_healthz_uses_count_sessions_async(self, client, monkeypatch):
        async def fake_count_sessions_async() -> int:
            return 7

        def fail_list_sessions_async():
            raise AssertionError("healthz should not call list_sessions_async")

        monkeypatch.setattr(
            session_manager,
            "count_sessions_async",
            fake_count_sessions_async,
        )
        monkeypatch.setattr(
            session_manager,
            "list_sessions_async",
            fail_list_sessions_async,
        )

        health = await client.get("/healthz")

        assert health.status_code == 200
        assert health.json()["sessions"] == 7

    async def test_readyz_reports_dependencies_ready(self, client):
        ready = await client.get("/readyz")

        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "checks": {"session_store": "ok", "rate_limiter": "ok"},
        }

    async def test_readyz_returns_503_when_session_store_unhealthy(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(session_manager._store, "check_health", lambda: False)

        ready = await client.get("/readyz")

        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "checks": {"session_store": "error", "rate_limiter": "ok"},
        }

    async def test_readyz_reports_configured_cloud_workspace_provider_when_ready(
        self, client, monkeypatch
    ):
        seen_configs: list[dict[str, object]] = []
        to_thread_calls: list[tuple[Callable[..., bool], tuple[object, ...]]] = []

        def fake_readiness(config: dict[str, object]) -> bool:
            seen_configs.append(dict(config))
            return True

        async def fake_to_thread(func: Callable[..., bool], *args: object) -> bool:
            to_thread_calls.append((func, args))
            return func(*args)

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/srv/coding-agent/workspaces",
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cloud_workspace_ready_from_config",
            fake_readiness,
            raising=False,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.asyncio.to_thread",
            fake_to_thread,
        )

        ready = await client.get("/readyz")

        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "checks": {
                "session_store": "ok",
                "rate_limiter": "ok",
                "cloud_workspace": "ok",
            },
        }
        assert seen_configs == [
            {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/srv/coding-agent/workspaces",
            }
        ]
        assert to_thread_calls == [
            (
                fake_readiness,
                (
                    {
                        "enabled": True,
                        "provider": "docker",
                        "workspace_root": "/srv/coding-agent/workspaces",
                    },
                ),
            )
        ]

    async def test_readyz_returns_503_when_cloud_workspace_provider_unhealthy(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/srv/coding-agent/workspaces",
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cloud_workspace_ready_from_config",
            lambda config: False,
            raising=False,
        )

        ready = await client.get("/readyz")

        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "checks": {
                "session_store": "ok",
                "rate_limiter": "ok",
                "cloud_workspace": "error",
            },
        }

    async def test_readyz_returns_503_when_enabled_cloud_workspace_config_is_invalid(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {"enabled": True},
        )

        ready = await client.get("/readyz")

        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "checks": {
                "session_store": "ok",
                "rate_limiter": "ok",
                "cloud_workspace": "error",
            },
        }

    async def test_create_session_uses_real_provider_by_default(self, client):
        response = await client.post("/sessions", json={})
        session_id = response.json()["session_id"]

        session = session_manager.get_session(session_id)

        assert session.provider is None

    async def test_create_session_stores_local_binding_by_default(self, client):
        response = await client.post("/sessions", json={})

        session = session_manager.get_session(response.json()["session_id"])

        assert isinstance(session.execution_binding, LocalExecutionBinding)
        assert session.execution_binding.workspace_root == str(Path.cwd().resolve())

    async def test_create_session_stores_local_binding_with_repo_path(
        self, client, tmp_path
    ):
        response = await client.post(
            "/sessions",
            json={"repo_path": str(tmp_path)},
        )

        session = session_manager.get_session(response.json()["session_id"])

        assert isinstance(session.execution_binding, LocalExecutionBinding)
        assert session.execution_binding.workspace_root == str(tmp_path.resolve())
        assert session.repo_path == tmp_path.resolve()

    async def test_http_create_session_stores_cloud_execution_binding(self, client):
        response = await client.post(
            "/sessions",
            json={
                "execution_binding": {
                    "kind": "cloud",
                    "workspace_url": "https://workspace.example.com",
                    "workspace_id": "ws-123",
                }
            },
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert isinstance(session.execution_binding, CloudWorkspaceBinding)
        assert (
            session.execution_binding.workspace_url == "https://workspace.example.com"
        )
        assert session.execution_binding.workspace_id == "ws-123"

    async def test_http_create_session_provisions_docker_cloud_workspace(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
            },
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert isinstance(session.execution_binding, CloudWorkspaceBinding)
        assert session.origin == {
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        }
        assert (tmp_path / session.execution_binding.workspace_id).is_dir()

        info_response = await client.get(f"/sessions/{response.json()['session_id']}")
        assert info_response.status_code == 200
        assert info_response.json()["origin"] == {
            "channel": "http",
            "binding_kind": "cloud",
            "workspace_source_kind": "docker",
        }

    async def test_create_session_rejects_conflicting_workspace_binding_inputs(
        self, client
    ):
        response = await client.post(
            "/sessions",
            json={
                "execution_binding": {
                    "kind": "cloud",
                    "workspace_url": "https://workspace.example.com",
                    "workspace_id": "ws-123",
                },
                "workspace_source": {"kind": "docker"},
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "execution_binding and workspace_source cannot be set together"
        )

    async def test_create_session_rejects_workspace_provisioning_when_disabled(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {},
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "cloud workspace provisioning requires cloud_workspace.enabled=true"
        )

    async def test_create_session_rolls_back_provisioned_workspace_on_failure(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-orphan/workspace",
            workspace_id="ws-orphan",
        )
        cleaned: list[CloudWorkspaceBinding] = []

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def fail_create_session(**kwargs):
            del kwargs
            raise RuntimeError("session store unavailable")

        monkeypatch.setattr(session_manager, "create_session", fail_create_session)
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 500
        assert cleaned == [binding]

    async def test_create_session_rolls_back_provisioned_workspace_on_non_runtime_failure(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-orphan/workspace",
            workspace_id="ws-orphan-nonruntime",
        )
        cleaned: list[CloudWorkspaceBinding] = []

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def fail_create_session(**kwargs):
            del kwargs
            raise KeyError("owner store unavailable")

        monkeypatch.setattr(session_manager, "create_session", fail_create_session)
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 500
        assert cleaned == [binding]

    async def test_create_session_keeps_original_failure_when_rollback_cleanup_fails(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-orphan/workspace",
            workspace_id="ws-orphan-cleanup-fails",
        )

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def fail_create_session(**kwargs):
            del kwargs
            raise RuntimeError("session store unavailable")

        def fail_cleanup(target_binding):
            del target_binding
            raise RuntimeError("cleanup unavailable")

        monkeypatch.setattr(session_manager, "create_session", fail_create_session)
        monkeypatch.setattr(
            "coding_agent.ui.http_server._cleanup_provisioned_cloud_binding",
            fail_cleanup,
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "session store unavailable"

    async def test_create_session_rolls_back_provisioned_workspace_on_cancellation(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-cancelled/workspace",
            workspace_id="ws-cancelled",
        )
        cleaned: list[CloudWorkspaceBinding] = []

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def cancel_create_session(**kwargs):
            del kwargs
            raise asyncio.CancelledError

        monkeypatch.setattr(session_manager, "create_session", cancel_create_session)
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        with pytest.raises(asyncio.CancelledError):
            await client.post(
                "/sessions",
                json={"workspace_source": {"kind": "docker"}},
            )

        assert cleaned == [binding]

    async def test_close_session_cleans_up_provisioned_workspace_on_delete(
        self, client, monkeypatch, tmp_path
    ):
        cleaned: list[CloudWorkspaceBinding] = []

        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        create_response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert create_response.status_code == 200
        binding = session_manager.get_session(
            create_response.json()["session_id"]
        ).execution_binding
        assert isinstance(binding, CloudWorkspaceBinding)

        close_response = await client.delete(
            f"/sessions/{create_response.json()['session_id']}"
        )

        assert close_response.status_code == 200
        assert cleaned == [binding]

    async def test_close_session_cleans_up_when_new_provisioning_is_disabled(
        self, client, monkeypatch, tmp_path
    ):
        cleaned: list[CloudWorkspaceBinding] = []
        config_enabled = True

        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )

        def cloud_workspace_config():
            return {
                "enabled": config_enabled,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
            }

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            cloud_workspace_config,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        create_response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )
        binding = session_manager.get_session(
            create_response.json()["session_id"]
        ).execution_binding
        assert isinstance(binding, CloudWorkspaceBinding)
        config_enabled = False

        close_response = await client.delete(
            f"/sessions/{create_response.json()['session_id']}"
        )

        assert close_response.status_code == 200
        assert cleaned == [binding]

    async def test_create_session_accepts_runtime_provider_metadata(self, client):
        response = await client.post(
            "/sessions",
            json={
                "provider": "anthropic",
                "model": "claude-test-http",
                "base_url": "http://llm.local/v1",
                "max_steps": 9,
            },
        )
        assert response.status_code == 200

        session = session_manager.get_session(response.json()["session_id"])

        assert session.provider is None
        assert session.provider_name == "anthropic"
        assert session.model_name == "claude-test-http"
        assert session.base_url == "http://llm.local/v1"
        assert session.max_steps == 9

        info_response = await client.get(f"/sessions/{response.json()['session_id']}")
        assert info_response.status_code == 200
        info = info_response.json()
        assert info["provider_name"] == "anthropic"
        assert info["model_name"] == "claude-test-http"
        assert info["base_url"] == "http://llm.local/v1"
        assert info["max_steps"] == 9

    async def test_create_session_rejects_invalid_runtime_provider(self, client):
        response = await client.post(
            "/sessions",
            json={"provider": "not-a-provider", "model": "test-model"},
        )

        assert response.status_code == 422

    async def test_send_prompt_uses_cloud_environment_from_provisioned_workspace(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )

        class _CreateAgentCapture:
            def __init__(self) -> None:
                self.environment: CloudEnvironment | None = None
                self.session_id: str | None = None
                self.workspace_root: object | None = None

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
            },
        )
        monkeypatch.setattr(
            session_manager,
            "_binding_resolver",
            _build_binding_resolver(),
        )

        captured = _CreateAgentCapture()

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, ctx
                self._consumer = consumer

            async def run_turn(self, prompt: str) -> None:
                del prompt
                assert captured.session_id is not None
                await self._consumer.emit(
                    TurnEnd(
                        session_id=captured.session_id,
                        agent_id="",
                        turn_id="turn-provisioned",
                        completion_status=CompletionStatus.COMPLETED,
                    )
                )

        fake_pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(
                get=lambda _: types.SimpleNamespace(_instance=None)
            ),
            _directive_executor=None,
        )

        def fake_create_agent(**kwargs):
            environment = kwargs.get("environment")
            assert isinstance(environment, CloudEnvironment)
            session_id_override = kwargs.get("session_id_override")
            assert isinstance(session_id_override, str)
            captured.environment = environment
            captured.session_id = session_id_override
            captured.workspace_root = kwargs.get("workspace_root")
            return fake_pipeline, types.SimpleNamespace(config={}, tape=Tape())

        monkeypatch.setattr(session_manager, "_create_agent", fake_create_agent)
        monkeypatch.setattr(
            "coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter
        )

        create_response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )
        session_id = create_response.json()["session_id"]

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append(sse.event)
                if sse.event == "TurnEnd":
                    break

        assert captured.environment is not None
        binding = session_manager.get_session(session_id).execution_binding
        assert isinstance(binding, CloudWorkspaceBinding)
        assert (
            captured.environment.tool_config()["workspace_id"] == binding.workspace_id
        )
        assert captured.workspace_root is None
        assert events[-1] == "TurnEnd"

    def test_build_session_manager_enables_owner_store_for_pg_http_sessions(
        self, monkeypatch
    ):
        class FakeOwnerStore:
            def __init__(self, *, pg_pool) -> None:
                self.pg_pool = pg_pool

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_storage_config",
            lambda: {
                "http_session_backend": "pg",
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "dsn": "postgresql://example",
                "owner_id": "pod-a",
                "fencing_token": 9,
                "owner_lease_seconds": 40.0,
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.SessionOwnerStore",
            FakeOwnerStore,
        )

        manager = _build_session_manager()
        try:
            assert isinstance(manager._owner_store, FakeOwnerStore)
            assert manager._owner_store.pg_pool is manager._pg_pool
            assert manager._owner_id == "pod-a"
            assert manager._fencing_token == 9
            assert manager.owner_lease_seconds == 40.0
        finally:
            asyncio.run(manager.close())

    @pytest.mark.parametrize("fencing_token", [None, 0, -1, "9"])
    def test_build_session_manager_requires_explicit_positive_fencing_token_for_pg_http_sessions(
        self, monkeypatch, fencing_token
    ):
        storage_config = {
            "http_session_backend": "pg",
            "tape_backend": "pg",
            "checkpoint_backend": "pg",
            "dsn": "postgresql://example",
            "owner_id": "pod-a",
            "owner_lease_seconds": 40.0,
        }
        if fencing_token is not None:
            storage_config["fencing_token"] = fencing_token
        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_storage_config",
            lambda: storage_config,
        )

        with pytest.raises(ValueError, match="storage.fencing_token"):
            _build_session_manager()

    def test_build_session_manager_does_not_enable_owner_store_for_non_pg_http_sessions(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_storage_config",
            lambda: {
                "http_session_backend": "redis",
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "dsn": "postgresql://example",
            },
        )

        manager = _build_session_manager()

        assert manager._owner_store is None

    async def test_renew_owner_leases_exits_when_owner_leases_are_not_configured(
        self, monkeypatch
    ):
        events: list[str] = []

        async def fail_sleep(delay: float) -> None:
            del delay
            events.append("sleep")
            raise AssertionError("renew loop should not sleep without owner leases")

        async def fail_renew_owner_leases() -> None:
            events.append("renew")
            raise AssertionError("renew loop should not renew without owner leases")

        session_manager.configure_owner_leases(
            owner_store=None,
            owner_id=None,
            fencing_token=None,
        )
        monkeypatch.setattr(
            session_manager,
            "renew_owner_leases",
            fail_renew_owner_leases,
        )
        monkeypatch.setattr("coding_agent.ui.http_server.asyncio.sleep", fail_sleep)

        await _renew_owner_leases()

        assert events == []

    async def test_renew_owner_leases_renews_current_sessions(self, monkeypatch):
        renew_calls: list[tuple[str, str, float, int, int]] = []

        class FakeOwnerStore:
            def __init__(self) -> None:
                self._owners = {
                    "session-a": SessionOwnerRecord(
                        owner_id="pod-a",
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=40),
                        fencing_token=9,
                    ),
                    "session-b": SessionOwnerRecord(
                        owner_id="pod-a",
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=40),
                        fencing_token=9,
                    ),
                }

            async def acquire(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                fencing_token: int = 1,
            ) -> bool:
                del session_id, owner_id, lease_seconds, fencing_token
                return True

            async def renew(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                new_fencing_token: int = 2,
                current_fencing_token: int = 1,
            ) -> bool:
                renew_calls.append(
                    (
                        session_id,
                        owner_id,
                        lease_seconds,
                        new_fencing_token,
                        current_fencing_token,
                    )
                )
                return True

            async def release(
                self,
                session_id: str,
                owner_id: str,
                fencing_token: int,
            ) -> bool:
                del session_id, owner_id, fencing_token
                return True

            async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
                return self._owners.get(session_id)

        sleep_calls = 0

        async def fake_sleep(delay: float) -> None:
            nonlocal sleep_calls
            assert delay == 20.0
            sleep_calls += 1
            if sleep_calls == 1:
                raise asyncio.CancelledError

        async def fake_list_sessions_async() -> list[str]:
            return ["session-a", "session-b"]

        session_manager.configure_owner_leases(
            owner_store=FakeOwnerStore(),
            owner_id="pod-a",
            fencing_token=9,
            owner_lease_seconds=40.0,
        )
        monkeypatch.setattr(
            session_manager,
            "list_sessions_async",
            fake_list_sessions_async,
        )
        monkeypatch.setattr("coding_agent.ui.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await _renew_owner_leases()

        assert renew_calls == [
            ("session-a", "pod-a", 40.0, 9, 9),
            ("session-b", "pod-a", 40.0, 9, 9),
        ]

    async def test_renew_owner_leases_logs_and_continues_after_failure(
        self, monkeypatch, caplog
    ):
        renew_calls = 0

        async def fake_renew_owner_leases() -> None:
            nonlocal renew_calls
            renew_calls += 1
            if renew_calls == 1:
                raise RuntimeError("database temporarily unavailable")

        sleep_calls = 0

        async def fake_sleep(delay: float) -> None:
            nonlocal sleep_calls
            assert delay == 15.0
            sleep_calls += 1
            if sleep_calls == 2:
                raise asyncio.CancelledError

        session_manager.configure_owner_leases(
            owner_store=cast(Any, object()),
            owner_id="pod-a",
            fencing_token=9,
            owner_lease_seconds=30.0,
        )
        monkeypatch.setattr(
            session_manager,
            "renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr("coding_agent.ui.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await _renew_owner_leases()

        assert renew_calls == 2
        assert "Error renewing owner leases" in caplog.text

    async def test_renew_owner_leases_renews_before_first_sleep(self, monkeypatch):
        events: list[str] = []

        async def fake_renew_owner_leases() -> None:
            events.append("renew")

        async def fake_sleep(delay: float) -> None:
            assert delay == 15.0
            events.append("sleep")
            raise asyncio.CancelledError

        session_manager.configure_owner_leases(
            owner_store=cast(Any, object()),
            owner_id="pod-a",
            fencing_token=9,
            owner_lease_seconds=30.0,
        )
        monkeypatch.setattr(
            session_manager,
            "renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr("coding_agent.ui.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await _renew_owner_leases()

        assert events == ["renew", "sleep"]


class TestPromptStreaming:
    """Tests for prompt streaming endpoint."""

    async def test_prompt_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.post(
            "/sessions/nonexistent/prompt",
            json={"prompt": "test"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_prompt_missing_session_returns_404_before_owner_check(self, client):
        class FailingOwnerStore:
            async def acquire(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                fencing_token: int = 1,
            ) -> bool:
                del session_id, owner_id, lease_seconds, fencing_token
                return True

            async def renew(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                new_fencing_token: int = 2,
                current_fencing_token: int = 1,
            ) -> bool:
                del (
                    session_id,
                    owner_id,
                    lease_seconds,
                    new_fencing_token,
                    current_fencing_token,
                )
                return True

            async def release(
                self,
                session_id: str,
                owner_id: str,
                fencing_token: int,
            ) -> bool:
                del session_id, owner_id, fencing_token
                return True

            async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
                raise AssertionError(f"owner check should not run for {session_id}")

        session_manager.configure_owner_leases(
            owner_store=FailingOwnerStore(),
            owner_id="owner-a",
            fencing_token=7,
        )

        response = await client.post(
            "/sessions/missing-session/prompt",
            json={"prompt": "Hello"},
        )

        assert response.status_code == 404

    async def test_prompt_returns_409_for_stale_owner_before_streaming(self, client):
        class FakeOwnerStore:
            async def acquire(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                fencing_token: int = 1,
            ) -> bool:
                del session_id, owner_id, lease_seconds, fencing_token
                return True

            async def renew(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                new_fencing_token: int = 2,
                current_fencing_token: int = 1,
            ) -> bool:
                del (
                    session_id,
                    owner_id,
                    lease_seconds,
                    new_fencing_token,
                    current_fencing_token,
                )
                return True

            async def release(
                self,
                session_id: str,
                owner_id: str,
                fencing_token: int,
            ) -> bool:
                del session_id, owner_id, fencing_token
                return True

            async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
                del session_id
                return SessionOwnerRecord(
                    owner_id="other-owner",
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    fencing_token=8,
                )

        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session_manager.configure_owner_leases(
            owner_store=FakeOwnerStore(),
            owner_id="owner-a",
            fencing_token=7,
        )

        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale owner or fencing token rejected"
        assert not session_manager.get_session(session_id).turn_in_progress

    async def test_prompt_streaming_events(self, client):
        """Test that prompt returns SSE events."""
        # Create session first
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Send prompt and collect SSE events
        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "TurnEnd" and not events[-1]["data"]["agent_id"]:
                    break

        # Verify events
        assert len(events) > 0
        assert events[-1]["event"] == "TurnEnd"
        assert events[-1]["data"]["completion_status"] in {
            CompletionStatus.COMPLETED.value,
            CompletionStatus.BLOCKED.value,
            CompletionStatus.ERROR.value,
        }

    async def test_prompt_streams_owner_conflict_as_error_event_without_fake_turn(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def conflicting_run_agent(_session_id: str, _prompt: str) -> None:
            assert _session_id == session_id
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        monkeypatch.setattr(session_manager, "run_agent", conflicting_run_agent)

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                break

        assert [event["event"] for event in events] == ["Error"]
        assert events[0]["data"]["error"] == "stale owner or fencing token rejected"

    async def test_prompt_returns_parent_turn_end_when_agent_bootstrap_fails(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        with patch(
            "coding_agent.ui.session_manager.importlib.import_module"
        ) as import_module:
            import_module.return_value = types.SimpleNamespace(
                create_agent=lambda **kwargs: (_ for _ in ()).throw(
                    RuntimeError("bootstrap exploded")
                )
            )

            events = []
            async with aconnect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt",
                json={"prompt": "Hello"},
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    events.append({"event": sse.event, "data": json.loads(sse.data)})
                    if sse.event == "TurnEnd":
                        break

        assert events[0]["event"] == "StreamDelta"
        assert "bootstrap exploded" in events[0]["data"]["content"]
        assert events[-1]["event"] == "TurnEnd"
        assert events[-1]["data"]["agent_id"] == ""
        assert events[-1]["data"]["completion_status"] == CompletionStatus.ERROR.value

    async def test_prompt_streams_fatal_tool_execution_error_as_error_event_without_fake_turn(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, consumer
                self.ctx = ctx

            async def run_turn(self, prompt: str) -> None:
                del prompt
                raise FatalToolExecutionError("fatal tool failure")

            async def close(self) -> None:
                return None

        fake_pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(
                get=lambda _: types.SimpleNamespace(_instance=None)
            ),
            _directive_executor=None,
        )

        monkeypatch.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (
                fake_pipeline,
                types.SimpleNamespace(config={}, tape=kwargs.get("tape") or Tape()),
            ),
        )
        monkeypatch.setattr(
            "coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter
        )

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event in {"Error", "TurnEnd"}:
                    break

        assert [event["event"] for event in events] == ["Error"]
        assert events[0]["data"]["error"] == "fatal tool failure"

    async def test_prompt_sets_turn_in_progress(self, client):
        """Test that prompt sets turn_in_progress flag."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run_agent(_session_id: str, _prompt: str) -> None:
            started.set()
            await release.wait()
            await session_manager.get_session(session_id).wire.send(
                TurnEnd(
                    session_id=session_id,
                    completion_status=CompletionStatus.COMPLETED,
                    turn_id="test-turn",
                )
            )

        # Start prompt in background
        async def send_prompt():
            async with aconnect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt",
                json={"prompt": "Hello"},
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if sse.event == "TurnEnd":
                        break

        # Check turn_in_progress during execution
        with patch.object(session_manager, "run_agent", side_effect=fake_run_agent):
            task = asyncio.create_task(send_prompt())
            await asyncio.wait_for(started.wait(), timeout=1)
            assert session_manager.get_session(session_id).turn_in_progress
            release.set()
            await task

        assert not session_manager.get_session(session_id).turn_in_progress

    async def test_prompt_surfaces_subagent_tool_failure_in_real_http_session(
        self, client, tmp_path
    ):
        class ScriptedSubagentProvider:
            def __init__(self) -> None:
                self.calls = 0

            @property
            def model_name(self) -> str:
                return "scripted-subagent"

            @property
            def max_context_size(self) -> int:
                return 128000

            async def stream(self, messages, tools=None, **kwargs):
                del messages, kwargs
                self.calls += 1
                if self.calls == 1:
                    yield ToolCallEvent(
                        tool_call_id="tc-http-subagent",
                        name="subagent",
                        arguments={"goal": "Inspect child task"},
                    )
                    yield DoneEvent()
                    return

                if self.calls == 2:
                    assert tools is not None
                    tool_names = {
                        tool["function"]["name"]
                        for tool in tools
                        if isinstance(tool, dict)
                        and isinstance(tool.get("function"), dict)
                    }
                    assert "subagent" not in tool_names
                    yield TextEvent(text="Child finished summary")
                    yield DoneEvent()
                    return

                yield TextEvent(text="Parent received child result")
                yield DoneEvent()

        provider = ScriptedSubagentProvider()
        session_id = "http-subagent-session"
        register_session(
            session_id,
            provider=provider,
            repo_path=tmp_path,
            approval_policy=ApprovalPolicy.YOLO,
        )

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Please delegate this to a subagent"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "TurnEnd" and not events[-1]["data"]["agent_id"]:
                    break

        assert any(
            event["event"] == "ToolCallDelta"
            and event["data"]["tool_name"] == "subagent"
            for event in events
        )
        assert any(
            event["event"] == "ToolResultDelta"
            and event["data"]["tool_name"] == "subagent"
            and event["data"]["display_result"]
            == "Subagent completed: Child finished summary"
            and event["data"]["is_error"] is False
            and event["data"]["result"] is None
            for event in events
        )
        assert any(
            event["event"] == "StreamDelta"
            and event["data"]["agent_id"] == "child-1"
            and event["data"]["content"] == "Child finished summary"
            for event in events
        )
        assert any(
            event["event"] == "StreamDelta"
            and event["data"]["agent_id"] == ""
            and event["data"]["content"] == "Parent received child result"
            for event in events
        )
        assert provider.calls == 3

    async def test_prompt_streams_fatal_subagent_summary_publish_as_error_event_in_real_http_session(
        self, client, tmp_path, monkeypatch
    ):
        class FatalSubagentProvider:
            def __init__(self) -> None:
                self.calls = 0

            @property
            def model_name(self) -> str:
                return "scripted-subagent-fatal"

            @property
            def max_context_size(self) -> int:
                return 128000

            async def stream(self, messages, tools=None, **kwargs):
                del messages, kwargs
                self.calls += 1
                if self.calls == 1:
                    yield ToolCallEvent(
                        tool_call_id="tc-http-subagent",
                        name="subagent",
                        arguments={"goal": "Inspect child task"},
                    )
                    yield DoneEvent()
                    return

                if self.calls == 2:
                    assert tools is not None
                    tool_names = {
                        tool["function"]["name"]
                        for tool in tools
                        if isinstance(tool, dict)
                        and isinstance(tool.get("function"), dict)
                    }
                    assert "subagent" not in tool_names
                    yield TextEvent(text="Child finished summary")
                    yield DoneEvent()
                    return

                yield TextEvent(text="Parent should not receive child result")
                yield DoneEvent()

        async def fatal_publish_subagent_message(
            session_id: str,
            text: str,
            *,
            message_id: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> bool:
            del session_id, text, message_id, metadata
            raise FatalToolExecutionError("fatal summary publish rejected")

        provider = FatalSubagentProvider()
        session_id = "http-subagent-fatal-session"
        session = register_session(
            session_id,
            provider=provider,
            repo_path=tmp_path,
            approval_policy=ApprovalPolicy.YOLO,
        )
        monkeypatch.setattr(
            session_manager,
            "publish_subagent_message",
            fatal_publish_subagent_message,
        )
        session.runtime_pipeline = None
        session.runtime_ctx = None
        session.runtime_adapter = None

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Please delegate this to a subagent"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "Error":
                    break

        assert any(
            event["event"] == "ToolCallDelta"
            and event["data"]["tool_name"] == "subagent"
            for event in events
        )
        assert any(
            event["event"] == "Error"
            and event["data"]["error"] == "fatal summary publish rejected"
            for event in events
        )
        assert provider.calls == 2


class TestConcurrentTurns:
    """Tests for 409 conflict on concurrent turns."""

    async def test_concurrent_turn_returns_409(self, client):
        """Test that concurrent turns return 409."""
        # Create session
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Manually set turn_in_progress
        session_manager.get_session(session_id).turn_in_progress = True

        # Try to send another prompt
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        )
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"].lower()

    async def test_turn_in_progress_cleared_after_completion(self, client):
        """Test that turn_in_progress is cleared after turn completes."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Complete a turn
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                if sse.event == "TurnEnd":
                    break

        # Should be able to start another turn
        assert not session_manager.get_session(session_id).turn_in_progress
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello again"},
        )
        assert response.status_code == 200


class TestApprovalEndpoint:
    """Tests for approval endpoint."""

    async def test_approve_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.post(
            "/sessions/nonexistent/approve",
            json={"request_id": "req1", "approved": True},
        )
        assert response.status_code == 404

    async def test_approve_no_pending_request(self, client):
        """Test 400 when no pending approval (legacy check)."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Without adding request to ApprovalStore or setting legacy pending_approval,
        # it will fail the legacy check (400) if legacy session exists
        # But if no legacy session, it should try ApprovalStore (which returns 404)
        # Since create_session creates both, we expect 400 from legacy check
        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req1", "approved": True},
        )
        # Legacy session exists and pending_approval is None -> 400
        assert response.status_code == 400
        assert "no pending" in response.json()["detail"].lower()

    async def test_approve_rejects_unknown_request_id(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        add_store_backed_approval_request(session, session_id, "correct_id")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "wrong_id", "approved": True},
        )
        assert response.status_code == 400
        assert "no pending approval request" in response.json()["detail"].lower()

    async def test_approve_returns_409_for_stale_owner_conflict(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        add_store_backed_approval_request(session, session_id, "req123")

        async def conflicting_submit_approval_response(**kwargs) -> ApprovalResponse:
            assert kwargs["session_id"] == session_id
            assert kwargs["request_id"] == "req123"
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        monkeypatch.setattr(
            session_manager,
            "submit_approval_response",
            conflicting_submit_approval_response,
        )

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale owner or fencing token rejected"

    async def test_approve_returns_500_without_internal_detail_for_unexpected_error(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_submit_approval_response(**kwargs) -> ApprovalResponse:
            assert kwargs["session_id"] == session_id
            assert kwargs["request_id"] == "req123"
            raise RuntimeError("secret internal failure")

        monkeypatch.setattr(
            session_manager,
            "submit_approval_response",
            failing_submit_approval_response,
        )

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"

    async def test_approve_success(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = None
        session.approval_event.clear()
        add_store_backed_approval_request(session, session_id, "req123")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "approved"
        assert session.approval_event.is_set()
        assert session.pending_approval is None

    async def test_approve_success_clears_pending_projection_for_coordinator_backed_request(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = None
        session.approval_event.clear()

        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call-req123",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)
        session.pending_approval = session.approval_coordinator.projection()

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )

        assert response.status_code == 200
        assert session.approval_event.is_set()
        assert session.pending_approval is None

    async def test_approve_retry_with_changed_body_uses_first_decision_before_waiter_consumes(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "ls"},
                call_id="call1",
            ),
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        first = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "first"},
        )
        retry = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": False, "feedback": "changed"},
        )

        assert first.status_code == 200
        assert retry.status_code == 200
        assert retry.json()["decision"] == "approved"
        response = await session.approval_coordinator.wait_for_response(
            "req123",
            timeout=0.01,
        )
        assert response is not None
        assert response.approved is True
        assert response.feedback == "first"

    async def test_approve_retry_with_changed_body_uses_first_decision_after_waiter_consumes(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "ls"},
                call_id="call1",
            ),
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        first = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "first"},
        )
        applied = await session.approval_coordinator.wait_for_response(
            "req123",
            timeout=0.01,
        )
        retry = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": False, "feedback": "changed"},
        )

        assert first.status_code == 200
        assert applied is not None
        assert applied.approved is True
        assert applied.feedback == "first"
        assert session.approval_store.get_request("req123") is None
        assert retry.status_code == 200
        assert retry.json()["decision"] == "approved"

    async def test_deny_success(self, client):
        """Test successful denial."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = {"request_id": "req123"}
        session.approval_event.clear()
        add_store_backed_approval_request(session, session_id, "req123")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": False, "feedback": "Too risky"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "denied"
        assert session.approval_event.is_set()
        assert session.pending_approval is None

    async def test_approve_rejects_stale_pending_projection_without_store_request(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = {"request_id": "req123"}
        session.approval_event.clear()
        assert session.approval_store.get_request("req123") is None

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )

        assert response.status_code == 400
        assert "no pending approval request" in response.json()["detail"].lower()
        assert session.pending_approval == {"request_id": "req123"}
        assert session.approval_event.is_set() is False

    async def test_approve_with_approval_store(self, client):
        """Test approval using ApprovalStore."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Add request to ApprovalStore directly (bypassing legacy check)
        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        session.pending_approval = None

        # Now approve via submit_approval (which uses ApprovalStore)
        success = await session_manager.submit_approval(
            session_id=session_id,
            request_id="req123",
            approved=True,
            feedback="Looks good",
        )
        assert success is True

    async def test_approve_endpoint_can_set_session_scope(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "req123",
                "approved": True,
                "feedback": "Looks good",
                "scope": "session",
            },
        )

        assert response.status_code == 200
        assert session.approval_coordinator.is_session_approved(
            ApprovalRequest(
                session_id=session_id,
                request_id="req456",
                tool_call=ToolCallDelta(
                    session_id=session_id,
                    tool_name="bash",
                    arguments={"command": "pwd"},
                    call_id="call2",
                ),
            )
        )

    async def test_approve_endpoint_query_params_can_set_session_scope(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        response = await client.post(
            f"/sessions/{session_id}/approve?request_id=req123&approved=true&scope=session"
        )

        assert response.status_code == 200
        assert session.approval_coordinator.is_session_approved(
            ApprovalRequest(
                session_id=session_id,
                request_id="req456",
                tool_call=ToolCallDelta(
                    session_id=session_id,
                    tool_name="bash",
                    arguments={"command": "pwd"},
                    call_id="call2",
                ),
            )
        )

    async def test_approve_endpoint_rejects_legacy_always_scope(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "ls"},
                call_id="call1",
            ),
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "req123",
                "approved": True,
                "feedback": "Looks good",
                "scope": "always",
            },
        )

        assert response.status_code == 422


class TestEventsFanOut:
    """Tests for SSE fan-out with multiple clients."""

    async def test_event_queues_registered(self, client):
        """Test that event queues are registered for fan-out."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Manually add queues to test fan-out
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        session = session_manager.get_session(session_id)
        session.event_queues = [queue1, queue2]

        # Broadcast an event
        test_event = {"event": "Test", "data": "{}"}
        await _broadcast_event(session, test_event)

        # Both queues should receive the event
        assert await queue1.get() == test_event
        assert await queue2.get() == test_event

    async def test_multiple_queues_in_session(self, client):
        """Test that a session can have multiple event queues."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Verify the session has event_queues list
        session = session_manager.get_session(session_id)
        assert hasattr(session, "event_queues")
        assert isinstance(session.event_queues, list)

    async def test_event_queue_cleanup_is_shielded_on_disconnect(self, monkeypatch):
        session_id = "disconnect-session"
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)

        cleanup_started = asyncio.Event()
        cleanup_released = asyncio.Event()
        cleaned: list[tuple[str, object]] = []

        async def fake_remove_event_queue_async(current_session_id: str, queue) -> None:
            cleaned.append((current_session_id, queue))
            cleanup_started.set()
            await cleanup_released.wait()

        monkeypatch.setattr(
            session_manager,
            "remove_event_queue_async",
            fake_remove_event_queue_async,
        )

        task = asyncio.create_task(
            _cleanup_event_queue_on_disconnect(session_id, queue)
        )
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        cleanup_released.set()
        await task

        assert len(cleaned) == 1
        assert cleaned == [(session_id, queue)]

    async def test_event_queue_cleanup_ignores_missing_session(self, monkeypatch):
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)

        async def fake_remove_event_queue_async(current_session_id: str, queue) -> None:
            _ = (current_session_id, queue)
            raise KeyError("Session not found: removed")

        monkeypatch.setattr(
            session_manager,
            "remove_event_queue_async",
            fake_remove_event_queue_async,
        )

        await _cleanup_event_queue_on_disconnect("removed", queue)

    async def test_event_generator_uses_public_session_apis_for_keepalive_exit(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        has_session_calls: list[str] = []
        get_session_calls: list[str] = []
        has_session_results = iter([True, False])

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        async def fake_has_session_async(current_session_id: str) -> bool:
            has_session_calls.append(current_session_id)
            return next(has_session_results)

        async def fake_get_session_async(current_session_id: str):
            get_session_calls.append(current_session_id)
            return session_manager.get_session(current_session_id)

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "get_session_async", fake_get_session_async
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        real_wait_for = asyncio.wait_for

        async def fake_wait_for(awaitable, timeout):
            if timeout == 30.0:
                awaitable.close()
                raise asyncio.TimeoutError
            return await real_wait_for(awaitable, timeout)

        monkeypatch.setattr(
            "coding_agent.ui.http_server.asyncio.wait_for", fake_wait_for
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/events",
                "headers": [],
            }
        )
        response = await get_events(request, session_id, None)
        event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)
        event = await anext(event_generator)

        assert event == {"event": "ping", "data": ""}

        with pytest.raises(StopAsyncIteration):
            await anext(event_generator)

        assert has_session_calls == [session_id, session_id]
        assert len(get_session_calls) >= 2
        assert all(
            call_session_id == session_id for call_session_id in get_session_calls
        )

    async def test_event_generator_exits_cleanly_when_session_disappears_during_keepalive(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        has_session_results = iter([True, True])

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        async def fake_has_session_async(current_session_id: str) -> bool:
            assert current_session_id == session_id
            return next(has_session_results)

        async def fake_has_event_queue_async(current_session_id: str, queue) -> bool:
            _ = queue
            assert current_session_id == session_id
            raise KeyError(f"Session not found: {session_id}")

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "has_event_queue_async", fake_has_event_queue_async
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        real_wait_for = asyncio.wait_for

        async def fake_wait_for(awaitable, timeout):
            if timeout == 30.0:
                awaitable.close()
                raise asyncio.TimeoutError
            return await real_wait_for(awaitable, timeout)

        monkeypatch.setattr(
            "coding_agent.ui.http_server.asyncio.wait_for", fake_wait_for
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/events",
                "headers": [],
            }
        )
        response = await get_events(request, session_id, None)
        event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)

        with pytest.raises(StopAsyncIteration):
            await anext(event_generator)


class TestLifespanShutdown:
    async def test_lifespan_runs_startup_cloud_workspace_cleanup_when_configured(
        self, monkeypatch
    ):
        events: list[str] = []
        cloud_workspace_config = {
            "enabled": True,
            "provider": "docker",
            "cleanup_on_startup": True,
        }

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_renew_owner_leases() -> None:
            raise asyncio.CancelledError

        def fake_cleanup_stale(config: dict[str, object]) -> int:
            assert config == {**cloud_workspace_config, "_active_workspace_ids": []}
            events.append("startup-gc")
            return 2

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: cloud_workspace_config,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_stale_cloud_workspaces_from_config",
            fake_cleanup_stale,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert events == ["startup-gc", "close"]

    async def test_cloud_workspace_gc_excludes_active_cloud_sessions(self, monkeypatch):
        active_binding = CloudWorkspaceBinding(
            workspace_url="docker://agent-ws-active/workspace",
            workspace_id="ws-active",
        )
        session_id = await session_manager.create_session(
            origin={
                "binding_kind": "cloud",
                "workspace_source_kind": "docker",
            },
            execution_binding=active_binding,
        )
        seen_configs: list[dict[str, object]] = []

        def fake_cleanup_stale(config: dict[str, object]) -> int:
            seen_configs.append(dict(config))
            return 0

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "cleanup_on_startup": True,
            },
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_stale_cloud_workspaces_from_config",
            fake_cleanup_stale,
        )

        await http_server._cleanup_cloud_workspaces_on_startup()

        assert seen_configs == [
            {
                "enabled": True,
                "provider": "docker",
                "cleanup_on_startup": True,
                "_active_workspace_ids": ["ws-active"],
            }
        ]
        await session_manager.close_session(session_id)

    async def test_periodic_cloud_workspace_gc_runs_at_configured_interval(
        self, monkeypatch
    ):
        events: list[str] = []
        cloud_workspace_config = {
            "enabled": True,
            "provider": "docker",
            "gc_interval_seconds": 300,
            "max_workspace_age_seconds": 3600,
        }

        def fake_cleanup_stale(config: dict[str, object]) -> int:
            assert config == {**cloud_workspace_config, "_active_workspace_ids": []}
            events.append("periodic-gc")
            return 1

        async def fake_sleep(delay: float) -> None:
            events.append(f"sleep:{delay}")
            raise asyncio.CancelledError

        monkeypatch.setattr(
            "coding_agent.ui.http_server._load_cloud_workspace_config",
            lambda: cloud_workspace_config,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server.cleanup_stale_cloud_workspaces_from_config",
            fake_cleanup_stale,
        )
        monkeypatch.setattr("coding_agent.ui.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await http_server._cleanup_stale_cloud_workspaces_periodically()

        assert events == ["periodic-gc", "sleep:300.0"]

    def test_cloud_workspace_gc_interval_rejects_boolean_numeric_values(self):
        assert (
            http_server._cloud_workspace_gc_interval_seconds(
                {
                    "enabled": True,
                    "gc_interval_seconds": True,
                    "max_workspace_age_seconds": 3600,
                }
            )
            is None
        )
        assert (
            http_server._cloud_workspace_gc_interval_seconds(
                {
                    "enabled": True,
                    "gc_interval_seconds": 300,
                    "max_workspace_age_seconds": True,
                }
            )
            is None
        )

    async def test_lifespan_shutdown_continues_after_session_failure(self, monkeypatch):
        observed_shutdowns: list[str] = []
        close_calls: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_list_sessions_async() -> list[str]:
            return ["session-a", "session-b"]

        async def fake_shutdown_session_runtime(session_id: str) -> None:
            observed_shutdowns.append(session_id)
            if session_id == "session-a":
                raise RuntimeError("boom")

        async def fake_close() -> None:
            close_calls.append("closed")

        monkeypatch.setattr(
            "coding_agent.ui.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(
            session_manager,
            "shutdown_session_runtime",
            fake_shutdown_session_runtime,
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await cm.__aexit__(None, None, None)

        assert observed_shutdowns == ["session-a", "session-b"]
        assert close_calls == ["closed"]

    async def test_lifespan_shutdown_logs_failed_owner_renew_task(
        self, monkeypatch, caplog
    ):
        close_calls: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_renew_owner_leases() -> None:
            raise RuntimeError("renew task failed before shutdown")

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            close_calls.append("closed")

        monkeypatch.setattr(
            "coding_agent.ui.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert close_calls == ["closed"]
        assert "Owner lease renewal task failed during shutdown" in caplog.text

    async def test_lifespan_backfills_owner_leases_before_renewal(self, monkeypatch):
        events: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_backfill_owner_leases() -> None:
            events.append("backfill")

        async def fake_renew_owner_leases() -> None:
            events.append("renew")
            raise asyncio.CancelledError

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.ui.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager,
            "backfill_owner_leases",
            fake_backfill_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert events == ["backfill", "renew", "close"]

    async def test_lifespan_logs_backfill_failure_and_still_starts(
        self, monkeypatch, caplog
    ):
        events: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_backfill_owner_leases() -> None:
            events.append("backfill")
            raise RuntimeError("backfill failed")

        async def fake_renew_owner_leases() -> None:
            events.append("renew")
            raise asyncio.CancelledError

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.ui.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.ui.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager,
            "backfill_owner_leases",
            fake_backfill_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert events == ["backfill", "renew", "close"]
        assert "Failed to backfill owner leases during startup" in caplog.text


class TestGetSession:
    """Tests for get session endpoint."""

    async def test_list_sessions_returns_only_sessions_owned_by_user_token(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        first = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer user-token-a"},
            json={},
        )
        second = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        assert first.status_code == 200
        assert second.status_code == 200

        response = await client.get(
            "/sessions",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 200
        assert [item["session_id"] for item in response.json()["sessions"]] == [
            first.json()["session_id"]
        ]

    async def test_list_sessions_returns_all_sessions_for_admin_token(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        first = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer user-token-a"},
            json={},
        )
        second = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        response = await client.get(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
        )

        assert response.status_code == 200
        assert {item["session_id"] for item in response.json()["sessions"]} == {
            first.json()["session_id"],
            second.json()["session_id"],
        }

    async def test_get_session_response_includes_status_and_workspace_summary(
        self, client: AsyncClient
    ):
        create_resp = await client.post(
            "/sessions",
            json={
                "execution_binding": {
                    "kind": "cloud",
                    "workspace_url": "docker://agent-ws-owned/workspace",
                    "workspace_id": "ws-owned",
                },
                "provider": "openai",
                "model": "test-model",
                "max_steps": 7,
            },
        )
        session_id = create_resp.json()["session_id"]

        response = await client.get(f"/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["id"] == session_id
        assert data["status"] == "created"
        assert data["turn_status"] == "idle"
        assert data["execution_binding"]["kind"] == "cloud"
        assert data["workspace_id"] == "ws-owned"
        assert data["provider_name"] == "openai"
        assert data["model_name"] == "test-model"
        assert data["max_steps"] == 7

    async def test_get_session_hides_other_user_session(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        created = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        response = await client.get(
            f"/sessions/{created.json()['session_id']}",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 404

    async def test_get_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.get("/sessions/nonexistent")
        assert response.status_code == 404

    async def test_get_session_success(self, client):
        """Test getting session details."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert "created_at" in data
        assert "last_activity" in data
        assert "turn_in_progress" in data
        assert "pending_approval" in data


class TestCloseSession:
    """Tests for close session endpoint."""

    async def test_close_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.delete("/sessions/nonexistent")
        assert response.status_code == 404

    async def test_close_session_success(self, client):
        """Test closing a session."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "closed"
        assert data["session_id"] == session_id
        assert not session_manager.has_session(session_id)

    async def test_close_session_broadcasts_event(self, client):
        """Test that closing session broadcasts to event queues."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Add a queue to receive events
        queue = asyncio.Queue()
        session_manager.get_session(session_id).event_queues = [queue]

        # Close the session
        await client.delete(f"/sessions/{session_id}")

        # The queue should have received SessionClosed event
        received_events = []
        while not queue.empty():
            received_events.append(await queue.get())

        assert any(e["event"] == "SessionClosed" for e in received_events)

    async def test_close_session_returns_error_when_manager_close_fails(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise RuntimeError("close exploded")

        monkeypatch.setattr(session_manager, "close_session", failing_close_session)

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"

    async def test_close_session_returns_404_when_session_disappears_during_close(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def fake_has_session_async(current_session_id: str) -> bool:
            assert current_session_id == session_id
            return True

        async def fake_get_session_async(current_session_id: str):
            assert current_session_id == session_id
            return session_manager.get_session(current_session_id)

        async def disappearing_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise KeyError(f"Session not found: {session_id}")

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "get_session_async", fake_get_session_async
        )
        monkeypatch.setattr(
            session_manager, "close_session", disappearing_close_session
        )

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == f"Session not found: {session_id}"

    async def test_close_session_returns_409_for_stale_owner_conflict(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def conflicting_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        monkeypatch.setattr(session_manager, "close_session", conflicting_close_session)

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 409
        assert response.json()["detail"] == "stale owner or fencing token rejected"

    async def test_close_session_returns_404_when_session_disappears_before_load(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def fake_has_session_async(current_session_id: str) -> bool:
            assert current_session_id == session_id
            return True

        async def disappearing_get_session_async(current_session_id: str):
            assert current_session_id == session_id
            raise KeyError(f"Session not found: {session_id}")

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "get_session_async", disappearing_get_session_async
        )

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == f"Session not found: {session_id}"

    async def test_close_session_hides_unexpected_internal_error_detail(
        self, client, monkeypatch, caplog
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise RuntimeError("dsn=postgresql://user:secret@example/db")

        monkeypatch.setattr(session_manager, "close_session", failing_close_session)

        with caplog.at_level("ERROR"):
            response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"
        assert "dsn=postgresql://user:secret@example/db" not in response.text
        assert "Unexpected error while closing session" in caplog.text


class TestSessionTimeout:
    """Tests for session idle timeout."""

    async def test_session_marked_expired_after_timeout(self):
        """Test that old sessions are marked for cleanup."""
        session_id = "test_session"
        old_time = datetime.now() - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES + 1)
        session = register_session(
            session_id,
            created_at=old_time,
            last_activity=old_time,
        )

        # Check that session is old enough to expire
        now = datetime.now()
        idle_time = now - session.last_activity
        assert idle_time > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)

    async def test_session_not_expired_if_active(self):
        """Test that active sessions are not expired."""
        session_id = "test_session"
        session = register_session(session_id)

        now = datetime.now()
        idle_time = now - session.last_activity
        assert idle_time < timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)


class TestWireMessageConversion:
    """Tests for wire message to SSE event conversion."""

    def test_turn_end_conversion(self):
        """Test TurnEnd message conversion."""
        msg = TurnEnd(
            session_id="test123",
            turn_id="turn456",
            completion_status=CompletionStatus.COMPLETED,
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "TurnEnd"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["turn_id"] == "turn456"
        assert data["completion_status"] == "completed"

    def test_stream_delta_conversion(self):
        """Test StreamDelta message conversion."""
        msg = StreamDelta(
            session_id="test123",
            agent_id="child-1",
            content="Hello world",
            role="assistant",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "StreamDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-1"
        assert data["content"] == "Hello world"
        assert data["role"] == "assistant"

    def test_tool_call_delta_conversion(self):
        """Test ToolCallDelta message conversion."""
        msg = ToolCallDelta(
            session_id="test123",
            agent_id="child-2",
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ToolCallDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-2"
        assert data["tool_name"] == "bash"
        assert data["call_id"] == "call1"
        assert data["arguments"]["command"] == "ls"

    def test_tool_result_delta_conversion_redacts_raw_result_payload(self):
        msg = ToolResultDelta(
            session_id="test123",
            agent_id="child-3",
            call_id="call1",
            tool_name="bash_run",
            result={"stdout": "SECRET=abc123", "stderr": "", "exit_code": 0},
            display_result="command succeeded",
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "ToolResultDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-3"
        assert data["call_id"] == "call1"
        assert data["tool_name"] == "bash_run"
        assert data["display_result"] == "command succeeded"
        assert data["is_error"] is False
        assert data["result"] is None

    def test_approval_request_conversion(self):
        """Test ApprovalRequest message conversion."""
        tool_call = ToolCallDelta(
            session_id="test123",
            agent_id="child-4",
            tool_name="bash",
            arguments={"command": "rm -rf /"},
            call_id="call1",
        )
        msg = ApprovalRequest(
            session_id="test123",
            agent_id="child-4",
            request_id="req1",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ApprovalRequest"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-4"
        assert data["request_id"] == "req1"
        assert data["timeout_seconds"] == 120
        assert data["tool_call"]["tool_name"] == "bash"


class TestCheckpointErrorMapping:
    async def test_capture_checkpoint_returns_409_for_active_turn(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_capture(*args, **kwargs):
            raise RuntimeError("turn already in progress")

        monkeypatch.setattr(session_manager, "capture_checkpoint", failing_capture)

        response = await client.post(f"/sessions/{session_id}/checkpoints", json={})

        assert response.status_code == 409
        assert response.json()["detail"] == "turn already in progress"

    async def test_restore_checkpoint_returns_unquoted_keyerror_detail(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_restore(*args, **kwargs):
            raise KeyError("Checkpoint cp-missing not found")

        monkeypatch.setattr(session_manager, "restore_checkpoint", failing_restore)

        response = await client.post(
            f"/sessions/{session_id}/checkpoints/cp-missing/restore"
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Checkpoint cp-missing not found"

    async def test_restore_checkpoint_maps_typeerror_to_bad_request(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_restore(*args, **kwargs):
            raise TypeError("checkpoint session config is missing model_name")

        monkeypatch.setattr(session_manager, "restore_checkpoint", failing_restore)

        response = await client.post(
            f"/sessions/{session_id}/checkpoints/cp-invalid/restore"
        )

        assert response.status_code == 400
        assert (
            response.json()["detail"]
            == "checkpoint session config is missing model_name"
        )

    def test_approval_response_conversion(self):
        """Test ApprovalResponse conversion."""
        msg = ApprovalResponse(
            session_id="test123",
            agent_id="child-5",
            request_id="req1",
            approved=True,
            feedback="Looks good",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ApprovalResponse"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-5"
        assert data["request_id"] == "req1"
        assert data["approved"] is True
        assert data["feedback"] == "Looks good"

    def test_thinking_delta_conversion(self):
        msg = ThinkingDelta(
            session_id="test123",
            agent_id="child-6",
            text="reasoning about the next step",
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "ThinkingDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-6"
        assert data["text"] == "reasoning about the next step"

    def test_turn_status_delta_conversion(self):
        msg = TurnStatusDelta(
            session_id="test123",
            agent_id="child-7",
            phase="idle",
            elapsed_seconds=1.5,
            tokens_in=123,
            tokens_out=45,
            model_name="kimi-for-coding",
            context_percent=12.5,
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "TurnStatusDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-7"
        assert data["phase"] == "idle"
        assert data["elapsed_seconds"] == 1.5
        assert data["tokens_in"] == 123
        assert data["tokens_out"] == 45
        assert data["model_name"] == "kimi-for-coding"
        assert data["context_percent"] == 12.5


class TestWireStreamingBehavior:
    async def test_stream_wire_messages_does_not_stop_on_child_turn_end(self):
        wire = LocalWire("parent-session")

        async def produce() -> None:
            await wire.send(
                TurnEnd(
                    session_id="parent-session",
                    agent_id="child-1",
                    turn_id="child-turn",
                    completion_status=CompletionStatus.COMPLETED,
                )
            )
            await wire.send(
                ToolResultDelta(
                    session_id="parent-session",
                    tool_name="subagent",
                    call_id="tc-subagent",
                    result="Subagent completed: Child finished summary",
                    display_result="Subagent completed: Child finished summary",
                )
            )
            await wire.send(
                TurnEnd(
                    session_id="parent-session",
                    agent_id="",
                    turn_id="parent-turn",
                    completion_status=CompletionStatus.COMPLETED,
                )
            )

        producer = asyncio.create_task(produce())
        events = []
        async for event in stream_wire_messages(wire):
            events.append(event)
        await producer

        assert [event["event"] for event in events] == [
            "TurnEnd",
            "ToolResultDelta",
            "TurnEnd",
        ]

    async def test_stream_wire_messages_reports_task_failure_before_wire_output(self):
        wire = LocalWire("parent-session")

        async def fail_before_output() -> None:
            raise RuntimeError("owner rejected")

        producer = asyncio.create_task(fail_before_output())
        events = []
        async for event in stream_wire_messages(wire, producer):
            events.append(event)

        assert [event["event"] for event in events] == ["Error"]
        assert "owner rejected" in json.loads(events[0]["data"])["error"]


class TestSessionToDict:
    """Tests for session serialization."""

    def test_session_to_dict(self):
        """Test session state to dictionary conversion."""
        session = Session(
            id="test123",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            last_activity=datetime(2024, 1, 1, 12, 30, 0),
            turn_in_progress=True,
            pending_approval={"call_id": "req1"},
        )
        data = _session_to_dict(session)
        assert data["id"] == "test123"
        assert data["turn_in_progress"] is True
        assert data["pending_approval"] is True
        assert "2024-01-01" in data["created_at"]


class TestBroadcastEvent:
    """Tests for event broadcasting."""

    async def test_broadcast_to_multiple_queues(self):
        """Test that events are broadcast to all queues."""
        session = register_session("test")
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        session.event_queues = [queue1, queue2]

        event = {"event": "Test", "data": "{}"}
        await _broadcast_event(session, event)

        assert await queue1.get() == event
        assert await queue2.get() == event

    async def test_broadcast_uses_provided_session_without_manager_lookup(self):
        session = register_session("broadcast-without-lookup")
        queue = asyncio.Queue()
        session.event_queues = [queue]
        event = {"event": "Test", "data": "{}"}

        with patch.object(
            session_manager,
            "broadcast_event",
            side_effect=AssertionError("manager lookup should be skipped"),
        ):
            await _broadcast_event(session, event)

        assert await queue.get() == event

    async def test_broadcast_prunes_full_queue_without_blocking(self):
        session = register_session("broadcast-full-queue")
        full_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        await full_queue.put({"event": "Old", "data": "{}"})
        healthy_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        session.event_queues = [full_queue, healthy_queue]
        event = {"event": "Test", "data": "{}"}

        await _broadcast_event(session, event)

        assert session.event_queues == [healthy_queue]
        assert full_queue.qsize() == 1
        assert await healthy_queue.get() == event

    async def test_broadcast_prunes_failed_queue(self):
        session = register_session("broadcast-failed-queue")

        class BrokenQueue:
            def put_nowait(self, item: object) -> None:
                _ = item
                raise RuntimeError("queue closed")

        healthy_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        broken_queue = cast(asyncio.Queue[dict[str, str]], cast(object, BrokenQueue()))
        session.event_queues = [broken_queue, healthy_queue]
        event = {"event": "Test", "data": "{}"}

        await _broadcast_event(session, event)

        assert session.event_queues == [healthy_queue]
        assert await healthy_queue.get() == event


class TestWaitForApproval:
    """Tests for the approval wait function."""

    async def test_wait_for_approval_session_not_found(self):
        """Test handling when session doesn't exist."""
        tool_call = ToolCallDelta(
            session_id="nonexistent",
            tool_name="bash",
            arguments={},
            call_id="call1",
        )
        req = ApprovalRequest(
            session_id="nonexistent",
            request_id="req1",
            tool_call=tool_call,
        )
        response = await wait_for_approval("nonexistent", req)
        assert isinstance(response, ApprovalResponse)
        assert response.approved is False
        assert response.feedback == "Session not found"

    async def test_wait_for_approval_timeout(self):
        """Test that approval times out correctly."""
        session_id = "test_session"
        register_session(session_id, turn_in_progress=True)

        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={},
            call_id="call1",
        )
        req = ApprovalRequest(
            session_id=session_id,
            request_id="req1",
            tool_call=tool_call,
        )

        # Use a very short timeout for testing
        import coding_agent.ui.http_server as http_server

        original_timeout = http_server.APPROVAL_TIMEOUT_SECONDS
        http_server.APPROVAL_TIMEOUT_SECONDS = 0.1

        try:
            response = await wait_for_approval(session_id, req)
            assert response.approved is False
            assert response.feedback is not None
            assert "timeout" in response.feedback.lower()
        finally:
            http_server.APPROVAL_TIMEOUT_SECONDS = original_timeout

    async def test_wait_for_approval_request_can_be_approved_via_http_endpoint(
        self, client
    ):
        import coding_agent.ui.http_server as http_server

        session_id = "http-wait-approval"
        register_session(session_id, turn_in_progress=True)

        req = ApprovalRequest(
            session_id=session_id,
            request_id="req-http-wait",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "pwd"},
                call_id="call-http-wait",
            ),
            timeout_seconds=1,
        )

        original_timeout = http_server.APPROVAL_TIMEOUT_SECONDS
        http_server.APPROVAL_TIMEOUT_SECONDS = 0.2

        try:
            wait_task = asyncio.create_task(wait_for_approval(session_id, req))
            for _ in range(20):
                if (
                    session_manager.get_session(
                        session_id
                    ).approval_coordinator.get_request("req-http-wait")
                    is not None
                ):
                    break
                await asyncio.sleep(0)
            else:
                pytest.fail("approval request was not registered")

            response = await client.post(
                f"/sessions/{session_id}/approve",
                json={
                    "request_id": "req-http-wait",
                    "approved": True,
                    "feedback": "approved over http",
                },
            )

            approval_response = await wait_task
        finally:
            http_server.APPROVAL_TIMEOUT_SECONDS = original_timeout

        assert response.status_code == 200, response.text
        assert approval_response.approved is True
        assert approval_response.feedback == "approved over http"


def test_http_server_import_falls_back_when_agent_toml_is_unreadable(
    monkeypatch,
) -> None:
    original_module = sys.modules.get("coding_agent.ui.http_server")
    monkeypatch.delitem(sys.modules, "coding_agent.ui.http_server", raising=False)

    try:
        with patch("agentkit.config.loader.load_config") as load_config:
            load_config.side_effect = ConfigError(
                "config file not found: /tmp/missing-agent.toml"
            )
            http_server = importlib.import_module("coding_agent.ui.http_server")

        assert http_server._load_storage_config() == {}
        assert http_server.session_manager._storage_config == {}
    finally:
        if original_module is None:
            monkeypatch.delitem(
                sys.modules, "coding_agent.ui.http_server", raising=False
            )
        else:
            sys.modules["coding_agent.ui.http_server"] = original_module


def test_http_server_import_raises_on_invalid_agent_toml(monkeypatch) -> None:
    original_module = sys.modules.get("coding_agent.ui.http_server")
    monkeypatch.delitem(sys.modules, "coding_agent.ui.http_server", raising=False)

    try:
        with patch("agentkit.config.loader.load_config") as load_config:
            load_config.side_effect = ConfigError("missing [agent] section")
            with pytest.raises(ConfigError, match=r"missing \[agent\] section"):
                importlib.import_module("coding_agent.ui.http_server")
    finally:
        if original_module is None:
            monkeypatch.delitem(
                sys.modules, "coding_agent.ui.http_server", raising=False
            )
        else:
            sys.modules["coding_agent.ui.http_server"] = original_module


class TestIntegration:
    """Integration tests for the full flow."""

    async def test_full_session_lifecycle(self, client):
        """Test full session lifecycle: create -> prompt -> get -> close."""
        # Create session
        response = await client.post("/sessions", json={})
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        # Get session info
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["id"] == session_id

        # Send prompt
        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append(sse.event)
                if sse.event == "TurnEnd":
                    break

        assert "TurnEnd" in events

        # Close session
        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "closed"

        # Verify session is gone
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 404


class TestCheckpointEndpoints:
    async def test_capture_checkpoint_returns_session_scoped_metadata(self, client):
        session_id = "capture-http-session"
        register_session(session_id)

        expected = CheckpointMeta(
            checkpoint_id="cp-http-capture",
            tape_id="stable-tape",
            session_id=session_id,
            entry_count=4,
            window_start=1,
            created_at=datetime(2026, 4, 16, 9, 30, 0),
            label="before-http-save",
        )

        async def fake_capture_checkpoint(
            requested_session_id: str,
            *,
            label: str | None = None,
            extra=None,
        ):
            assert requested_session_id == session_id
            assert label == "before-http-save"
            assert extra is None
            return expected

        with patch.object(
            session_manager,
            "capture_checkpoint",
            side_effect=fake_capture_checkpoint,
        ):
            response = await client.post(
                f"/sessions/{session_id}/checkpoints",
                json={"label": "before-http-save"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "checkpoint_id": "cp-http-capture",
            "tape_id": "stable-tape",
            "session_id": session_id,
            "entry_count": 4,
            "window_start": 1,
            "created_at": "2026-04-16T09:30:00",
            "label": "before-http-save",
        }

    async def test_capture_checkpoint_returns_404_for_unknown_session(self, client):
        response = await client.post(
            "/sessions/missing-session/checkpoints",
            json={"label": "before-http-save"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"

    async def test_list_checkpoints_returns_session_scoped_metadata(self, client):
        session_id = "checkpoint-http-session"
        register_session(session_id)

        expected = CheckpointMeta(
            checkpoint_id="cp-http-1",
            tape_id="stable-tape",
            session_id=session_id,
            entry_count=4,
            window_start=1,
            created_at=datetime(2026, 4, 16, 10, 0, 0),
            label="before-http-restore",
        )

        async def fake_list_checkpoints(requested_session_id: str):
            assert requested_session_id == session_id
            return [expected]

        with patch.object(
            session_manager,
            "list_checkpoints",
            side_effect=fake_list_checkpoints,
        ):
            response = await client.get(f"/sessions/{session_id}/checkpoints")

        assert response.status_code == 200
        assert response.json() == {
            "checkpoints": [
                {
                    "checkpoint_id": "cp-http-1",
                    "tape_id": "stable-tape",
                    "session_id": session_id,
                    "entry_count": 4,
                    "window_start": 1,
                    "created_at": "2026-04-16T10:00:00",
                    "label": "before-http-restore",
                }
            ]
        }

    async def test_list_checkpoints_returns_404_for_unknown_session(self, client):
        response = await client.get("/sessions/missing-session/checkpoints")

        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"

    async def test_restore_checkpoint_returns_ok_payload(self, client):
        session_id = "restore-http-session"
        register_session(session_id)

        async def fake_restore_checkpoint(
            requested_session_id: str, checkpoint_id: str
        ):
            assert requested_session_id == session_id
            assert checkpoint_id == "cp-http-restore"

        with patch.object(
            session_manager,
            "restore_checkpoint",
            side_effect=fake_restore_checkpoint,
        ):
            response = await client.post(
                f"/sessions/{session_id}/checkpoints/cp-http-restore/restore"
            )

        assert response.status_code == 200
        assert response.json() == {
            "status": "restored",
            "session_id": session_id,
            "checkpoint_id": "cp-http-restore",
        }

    async def test_restore_checkpoint_returns_409_for_active_turn(self, client):
        session_id = "restore-busy-session"
        register_session(session_id)

        async def fake_restore_checkpoint(
            requested_session_id: str, checkpoint_id: str
        ):
            del requested_session_id, checkpoint_id
            raise RuntimeError("turn already in progress")

        with patch.object(
            session_manager,
            "restore_checkpoint",
            side_effect=fake_restore_checkpoint,
        ):
            response = await client.post(
                f"/sessions/{session_id}/checkpoints/cp-busy/restore"
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "turn already in progress"


class TestApprovalStoreIntegration:
    """Tests for ApprovalStore integration in SessionManager and HTTP server."""

    async def test_session_has_approval_store(self, client):
        """Test that newly created sessions have an ApprovalStore."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        assert hasattr(session, "approval_store")
        assert isinstance(session.approval_store, ApprovalStore)

    async def test_approval_store_request_response(self, client):
        """Test that ApprovalStore can handle request/response cycle."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)

        # Add a request
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "echo test"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req-test",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        # Verify request was stored
        retrieved = session.approval_store.get_request("req-test")
        assert retrieved is not None
        assert retrieved.request_id == "req-test"

        # Respond to the request
        approval_resp = ApprovalResponse(
            session_id=session_id,
            request_id="req-test",
            approved=True,
            feedback="Approved",
        )
        success = session.approval_store.respond(approval_resp)
        assert success is True

    async def test_submit_approval_returns_bool(self, client):
        """Test that submit_approval returns boolean success status."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Try to approve non-existent request
        result = await session_manager.submit_approval(
            session_id=session_id,
            request_id="nonexistent",
            approved=True,
            feedback=None,
        )
        # Should return False since request wasn't added to store
        assert result is False

        # Now add the request and try again
        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id, tool_name="bash", arguments={}, call_id="call1"
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="real-req",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        result = await session_manager.submit_approval(
            session_id=session_id, request_id="real-req", approved=True, feedback="Good"
        )
        assert result is True

    async def test_close_session_cleans_up_approval_store(self, client):
        """Test that closing session removes approval store from manager."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Verify store exists
        assert session_id in session_manager._approval_stores

        # Close session
        await session_manager.close_session(session_id)

        # Store should be cleaned up
        assert session_id not in session_manager._approval_stores
