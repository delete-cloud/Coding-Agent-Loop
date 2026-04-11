from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from coding_agent.tools.sandbox import (
    SandboxConfig,
    SandboxLimits,
    SandboxRequest,
    SandboxUnavailableError,
    build_sandbox,
)
from coding_agent.tools.shell import bash_run


def _as_text(result: str | dict[str, str | int]) -> str:
    if not isinstance(result, str):
        raise TypeError(f"expected string result, got {type(result).__name__}")
    return result


class TestShellTool:
    def test_basic_command(self):
        result = bash_run(command="echo hello")
        assert "hello" in _as_text(result)

    def test_stderr_captured(self):
        result = bash_run(command="python -c \"import sys; sys.stderr.write('err')\"")
        assert "err" in _as_text(result)

    def test_exit_code_shown(self):
        result = bash_run(command='python -c "import sys; sys.exit(1)"')
        assert "Exit code: 1" in _as_text(result)

    def test_rejects_shell_metacharacters(self):
        result = bash_run(command="echo hello && rm -rf /tmp/nope")
        assert "unsupported shell syntax" in _as_text(result).lower()

    def test_rejects_shell_metacharacters_with_actionable_guidance(self):
        result = bash_run(command="which npx && npx --version")

        result_text = _as_text(result).lower()
        assert "run commands separately" in result_text
        assert "&&" in result_text

    def test_bash_run_routes_execution_through_sandbox_abstraction(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        captured: dict[str, object] = {}

        class FakeSandbox:
            def run(self, request: SandboxRequest) -> subprocess.CompletedProcess[str]:
                captured["request"] = request
                return subprocess.CompletedProcess(
                    args=request.args,
                    returncode=0,
                    stdout="sandbox stdout\n",
                    stderr="",
                )

        def fake_build_sandbox(config: SandboxConfig) -> FakeSandbox:
            captured["config"] = config
            return FakeSandbox()

        monkeypatch.setattr(
            "coding_agent.tools.shell.build_sandbox", fake_build_sandbox
        )

        pipeline_ctx = SimpleNamespace(
            config={
                "workspace_root": str(tmp_path),
                "shell": {
                    "sandbox_mode": "none",
                    "cpu_limit_seconds": 2,
                    "memory_limit_mb": 128,
                },
            }
        )

        result = bash_run(
            command="echo hello",
            timeout=7,
            cwd=str(tmp_path),
            env={"DEMO": "1"},
            __pipeline_ctx__=pipeline_ctx,
        )

        assert result == "sandbox stdout"
        config = captured["config"]
        assert isinstance(config, SandboxConfig)
        assert config.mode == "none"
        assert config.workspace_root == tmp_path.resolve()
        assert config.limits == SandboxLimits(cpu_limit_seconds=2, memory_limit_mb=128)
        request = captured["request"]
        assert isinstance(request, SandboxRequest)
        assert request.args == ["echo", "hello"]
        assert request.cwd == tmp_path.resolve()
        assert request.timeout_seconds == 7
        assert request.env is not None
        assert request.env["DEMO"] == "1"

    def test_build_sandbox_fails_clearly_when_docker_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        def missing_binary(name: str) -> None:
            del name
            return None

        monkeypatch.setattr("coding_agent.tools.sandbox.which", missing_binary)

        sandbox = build_sandbox(
            SandboxConfig(mode="docker", workspace_root=tmp_path.resolve())
        )

        with pytest.raises(SandboxUnavailableError, match="docker binary"):
            _ = sandbox.run(
                SandboxRequest(
                    args=["echo", "hello"],
                    cwd=tmp_path.resolve(),
                    env=None,
                    timeout_seconds=5,
                )
            )

    def test_none_sandbox_fails_clearly_for_unsupported_memory_limit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            "coding_agent.tools.sandbox.platform.system", lambda: "Darwin"
        )

        sandbox = build_sandbox(
            SandboxConfig(
                mode="none",
                workspace_root=tmp_path.resolve(),
                limits=SandboxLimits(memory_limit_mb=64),
            )
        )

        with pytest.raises(SandboxUnavailableError, match="memory limit"):
            _ = sandbox.run(
                SandboxRequest(
                    args=["echo", "hello"],
                    cwd=tmp_path.resolve(),
                    env=None,
                    timeout_seconds=5,
                )
            )

    def test_basic_command_ignores_inherited_default_memory_limit_on_macos(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            "coding_agent.tools.sandbox.platform.system", lambda: "Darwin"
        )
        monkeypatch.setattr(
            "coding_agent.tools.shell._default_shell_config",
            lambda: {
                "sandbox_mode": "none",
                "cpu_limit_seconds": 1,
                "memory_limit_mb": 64,
            },
        )

        result = bash_run(command="echo hello", cwd=str(tmp_path))

        assert result == "hello"
