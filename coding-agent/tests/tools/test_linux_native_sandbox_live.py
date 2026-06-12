from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from coding_agent.tools import sandbox as sandbox_module
from coding_agent.tools.shell import bash_run


def _ci_expects_bwrap() -> bool:
    return os.environ.get("CI_EXPECT_BWRAP") == "1"


def _bwrap_smoke() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "/usr/bin/true"],
        capture_output=True,
        text=True,
        timeout=5,
    )


def _require_linux_bwrap() -> None:
    if platform.system() != "Linux":
        if _ci_expects_bwrap():
            pytest.fail("CI_EXPECT_BWRAP=1 requires a Linux runner")
        pytest.skip("Linux native sandbox live tests require Linux")
    if shutil.which("bwrap") is None:
        if _ci_expects_bwrap():
            pytest.fail("CI_EXPECT_BWRAP=1 but bwrap is not installed")
        pytest.skip("Linux native sandbox live tests require bwrap")
    smoke = _bwrap_smoke()
    if smoke.returncode != 0:
        reason = smoke.stderr.strip() or smoke.stdout.strip() or "bwrap smoke failed"
        if _ci_expects_bwrap():
            pytest.fail(f"CI_EXPECT_BWRAP=1 but bwrap smoke failed: {reason}")
        pytest.skip(f"Linux native sandbox live tests require usable bwrap: {reason}")


@pytest.fixture
def workspace() -> Path:
    root = Path.cwd() / ".pytest-linux-bwrap" / uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _runner(
    workspace: Path,
    *,
    limits: sandbox_module.SandboxLimits | None = None,
) -> sandbox_module.LinuxNativeSandboxRunner:
    return sandbox_module.LinuxNativeSandboxRunner(
        sandbox_module.SandboxConfig(
            mode="native",
            workspace_root=workspace,
            limits=limits or sandbox_module.SandboxLimits(),
        )
    )


def _assert_success(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr


def test_ci_expected_bwrap_smoke() -> None:
    _require_linux_bwrap()

    _assert_success(_bwrap_smoke())


def test_bash_run_native_executes_with_bwrap(workspace: Path) -> None:
    _require_linux_bwrap()

    result = bash_run(
        command="python3 -c 'print(\"native-ok\")'",
        cwd=str(workspace),
        __pipeline_ctx__=SimpleNamespace(
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "native"},
            }
        ),
    )

    assert result == "native-ok"


def test_linux_native_uses_separate_loopback_network_namespace(
    workspace: Path,
) -> None:
    _require_linux_bwrap()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    try:
        result = _runner(workspace).run(
            sandbox_module.SandboxRequest(
                args=[
                    "python3",
                    "-c",
                    (
                        "import socket, sys; "
                        "sock = socket.socket(); sock.settimeout(1); "
                        f"sys.exit(0 if sock.connect_ex(('127.0.0.1', {port})) else 1)"
                    ),
                ],
                cwd=workspace,
                env=None,
                timeout_seconds=5,
            )
        )
    finally:
        listener.close()

    _assert_success(result)


def test_linux_native_applies_memory_limit(workspace: Path) -> None:
    _require_linux_bwrap()
    limit_mb = 512

    result = _runner(
        workspace,
        limits=sandbox_module.SandboxLimits(memory_limit_mb=limit_mb),
    ).run(
        sandbox_module.SandboxRequest(
            args=[
                "python3",
                "-c",
                ("import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"),
            ],
            cwd=workspace,
            env=None,
            timeout_seconds=5,
        )
    )

    _assert_success(result)
    assert int(result.stdout.strip()) == limit_mb * 1024 * 1024
