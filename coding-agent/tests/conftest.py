from __future__ import annotations

from collections.abc import AsyncIterator
import os
from pathlib import Path
from types import ModuleType

import pytest

import coding_agent.server.http_server as http_server
from coding_agent.local_storage import local_sqlite_storage_config
from coding_agent.observability import reset_prometheus_metrics
from coding_agent.server.session_manager import SessionManager


@pytest.fixture(autouse=True)
def ci_shell_sandbox_mode_override(monkeypatch: pytest.MonkeyPatch) -> None:
    mode = os.environ.get("CODING_AGENT_TEST_SHELL_SANDBOX_MODE")
    if mode is None:
        return
    if mode not in {"none", "native", "podman", "docker"}:
        raise ValueError(f"Unsupported test shell sandbox mode: {mode}")

    from coding_agent.tools import shell as shell_module

    cache_clear = getattr(shell_module._default_shell_config, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()

    def default_shell_config() -> dict[str, object]:
        return {"sandbox_mode": mode}

    monkeypatch.setattr(shell_module, "_default_shell_config", default_shell_config)


@pytest.fixture
async def isolated_http_session_manager(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[SessionManager]:
    """Install an unfenced HTTP SessionManager for tests that use sync cleanup."""
    test_module = request.module
    original_http_session_manager = http_server.session_manager
    had_module_session_manager = hasattr(test_module, "session_manager")
    original_module_session_manager = (
        getattr(test_module, "session_manager", None)
        if had_module_session_manager
        else None
    )
    test_session_manager = SessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        provisioned_cloud_binding_cleanup=http_server._cleanup_provisioned_cloud_binding,
    )
    http_server.session_manager = test_session_manager
    if isinstance(test_module, ModuleType) and had_module_session_manager:
        setattr(test_module, "session_manager", test_session_manager)

    reset_prometheus_metrics()
    test_session_manager.configure_owner_leases(
        owner_store=None,
        owner_id=None,
        fencing_token=None,
    )
    test_session_manager.configure_workspace_metadata_store(None)
    test_session_manager.configure_runtime_store(None)
    test_session_manager.clear_sessions()
    http_server.limiter.reset()
    try:
        yield test_session_manager
    finally:
        test_session_manager.configure_owner_leases(
            owner_store=None,
            owner_id=None,
            fencing_token=None,
        )
        test_session_manager.configure_workspace_metadata_store(None)
        test_session_manager.configure_runtime_store(None)
        SessionManager.clear_sessions(test_session_manager)
        reset_prometheus_metrics()
        http_server.limiter.reset()
        await SessionManager.close(test_session_manager)
        http_server.session_manager = original_http_session_manager
        if isinstance(test_module, ModuleType) and had_module_session_manager:
            setattr(test_module, "session_manager", original_module_session_manager)
