"""Tests for shell tool."""

import builtins
import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from coding_agent.tools.shell import bash_run
from coding_agent.tools import sandbox as sandbox_module


def _as_text(result: str | dict[str, str | int]) -> str:
    if not isinstance(result, str):
        raise TypeError(f"expected string result, got {type(result).__name__}")
    return result


class TestShellTool:
    """Tests for bash tool execution."""

    def test_basic_command(self):
        result = bash_run(command="echo hello")
        assert "hello" in _as_text(result)

    def test_command_with_args(self):
        result = bash_run(command="echo hello world")
        assert "hello world" in _as_text(result)

    def test_stderr_capture(self):
        result = bash_run(
            command="python3 -c 'import sys; sys.stderr.write(\"error message\")'"
        )
        result_text = _as_text(result)
        assert "STDERR" in result_text
        assert "error message" in result_text

    def test_non_zero_exit_code(self):
        result = bash_run(command="python3 -c 'exit(1)'")
        assert "Exit code: 1" in _as_text(result)

    def test_timeout(self):
        result = bash_run(command="sleep 10", timeout=1)
        assert "timed out" in _as_text(result).lower()

    def test_empty_command(self):
        result = bash_run(command="")
        result_text = _as_text(result).lower()
        assert "error" in result_text
        assert "empty" in result_text

    def test_output_truncation(self):
        result = bash_run(command="python3 -c \"print('x' * 20000)\"")
        assert "x" in _as_text(result)

    def test_special_characters_in_output(self):
        result = bash_run(command="printf 'hello\\nworld\\ttab'")
        result_text = _as_text(result)
        assert "hello" in result_text
        assert "world" in result_text

    def test_working_directory(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _ = (workspace / "test_file.txt").write_text("test content")

        pipeline_ctx = SimpleNamespace(
            config={"workspace_root": str(workspace), "shell": {"sandbox_mode": "none"}}
        )

        result = bash_run(
            command=f"cat {workspace / 'test_file.txt'}",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )
        assert "test content" in _as_text(result)

    def test_shlex_parsing(self):
        result = bash_run(command="echo 'hello world'")
        assert "hello world" in _as_text(result)

    def test_no_shell_injection_via_command_substitution(self):
        result = bash_run(command="echo $(echo secret)")
        result_text = _as_text(result)
        assert "$(echo secret)" in result_text or "secret" not in result_text

    def test_no_shell_injection_via_backticks(self):
        result = bash_run(command="echo `echo secret`")
        result_text = _as_text(result)
        assert "`echo secret`" in result_text or "secret" not in result_text

    def test_no_shell_injection_via_semicolon(self):
        result = bash_run(command="echo hello; echo world")
        result_text = _as_text(result)
        assert "hello;" in result_text or "world" not in result_text

    def test_quoted_arguments_preserved(self):
        result = bash_run(command='echo "quoted string"')
        assert "quoted string" in _as_text(result)

    def test_none_sandbox_mode_executes_through_abstraction(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _ = (workspace / "message.txt").write_text("hello from sandbox")

        pipeline_ctx = SimpleNamespace(
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "none", "cpu_limit_seconds": 1},
            }
        )

        result = bash_run(
            command="python3 -c 'from pathlib import Path; print(Path(\"message.txt\").read_text())'",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )
        assert _as_text(result) == "hello from sandbox"

    def test_none_sandbox_rejects_cwd_outside_workspace(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        pipeline_ctx = SimpleNamespace(
            config={"workspace_root": str(workspace), "shell": {"sandbox_mode": "none"}}
        )

        result = bash_run(
            command="pwd", cwd=str(tmp_path), __pipeline_ctx__=pipeline_ctx
        )
        assert "outside sandbox workspace" in _as_text(result).lower()

    def test_none_sandbox_blocks_absolute_path_escape(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        _ = outside.write_text("secret")

        pipeline_ctx = SimpleNamespace(
            config={"workspace_root": str(workspace), "shell": {"sandbox_mode": "none"}}
        )

        allowed = bash_run(
            command='python3 -c \'from pathlib import Path; (Path("inside.txt")).write_text("ok"); print(Path("inside.txt").read_text())\'',
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )
        blocked = bash_run(
            command=f"python3 -c 'from pathlib import Path; print(Path(r\"{outside}\").read_text())'",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )

        assert _as_text(allowed) == "ok"
        blocked_text = _as_text(blocked).lower()
        assert "error" in blocked_text
        assert "workspace" in blocked_text
        assert "secret" not in blocked_text

    def test_cd_outside_workspace_is_rejected(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        pipeline_ctx = SimpleNamespace(
            config={"workspace_root": str(workspace), "shell": {"sandbox_mode": "none"}}
        )
        result = bash_run(
            command="cd /tmp", cwd=str(workspace), __pipeline_ctx__=pipeline_ctx
        )
        assert "outside sandbox workspace" in _as_text(result).lower()

    def test_cd_within_workspace_succeeds(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        subdir = workspace / "sub"
        subdir.mkdir(parents=True)
        pipeline_ctx = SimpleNamespace(
            config={"workspace_root": str(workspace), "shell": {"sandbox_mode": "none"}}
        )
        result = bash_run(
            command="cd sub", cwd=str(workspace), __pipeline_ctx__=pipeline_ctx
        )
        assert "changed directory to" in _as_text(result).lower()
        assert str(subdir.resolve()) in _as_text(result)

    def test_export_updates_provided_env_dict(self):
        env: dict[str, str] = {}
        result = bash_run(command="export MY_VAR=hello", env=env)
        assert _as_text(result) == "Exported MY_VAR=hello"
        assert env["MY_VAR"] == "hello"

    def test_export_without_env_dict_still_returns_confirmation(self):
        result = bash_run(command="export MY_VAR=hello")
        assert _as_text(result) == "Exported MY_VAR=hello"

    def test_docker_sandbox_request_env_uses_only_explicit_env(self, monkeypatch):
        class FakeSandboxRequest:
            def __init__(self, *, args, cwd, env, timeout_seconds):
                self.args = args
                self.cwd = cwd
                self.env = env
                self.timeout_seconds = timeout_seconds

        class FakeSandbox:
            def run(self, request):
                assert request.args == ["echo", "hello"]
                assert request.env == {"EXPLICIT_ONLY": "1"}
                assert "HOST_ONLY" not in request.env
                return SimpleNamespace(stdout="ok", stderr="", returncode=0)

        fake_module = SimpleNamespace(
            SandboxRequest=FakeSandboxRequest,
<<<<<<< HEAD
            build_sandbox=lambda config: FakeSandbox(),
            SandboxLimits=lambda **kwargs: SimpleNamespace(**kwargs),
            SandboxConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            _validate_cwd=lambda cwd, workspace_root: None,
        )
        monkeypatch.setenv("HOST_ONLY", "host-value")
        monkeypatch.setattr(
            "coding_agent.tools.shell._load_sandbox_module", lambda: fake_module
=======
            SandboxConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            SandboxLimits=lambda **kwargs: SimpleNamespace(**kwargs),
            build_sandbox=lambda config: FakeSandbox(),
        )
        monkeypatch.setenv("HOST_ONLY", "host-value")
        monkeypatch.setattr(
            "coding_agent.tools.shell._load_sandbox_module",
            lambda: fake_module,
>>>>>>> c330af7 (fix(shell): keep sandbox env restricted to explicit vars)
        )

        result = bash_run(
            command="echo hello",
            env={"EXPLICIT_ONLY": "1"},
            __pipeline_ctx__=SimpleNamespace(
                config={"shell": {"sandbox_mode": "docker"}}
            ),
        )
<<<<<<< HEAD
        assert _as_text(result) == "ok"

    def test_docker_sandbox_request_env_is_explicit_only(
        self, monkeypatch, tmp_path: Path
    ):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.setenv("HOST_ONLY", "host-secret")

        captured: dict[str, object] = {}

        class FakeSandboxRequest:
            def __init__(self, *, args, cwd, env, timeout_seconds):
                captured["request"] = {
                    "args": args,
                    "cwd": cwd,
                    "env": env,
                    "timeout_seconds": timeout_seconds,
                }

        class FakeSandbox:
            def run(self, request):
                captured["run_request"] = request
                return SimpleNamespace(stdout="ok", stderr="", returncode=0)

        fake_module = SimpleNamespace(
            SandboxRequest=FakeSandboxRequest,
            build_sandbox=lambda config: FakeSandbox(),
            SandboxLimits=lambda **kwargs: SimpleNamespace(**kwargs),
            SandboxConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            _validate_cwd=lambda cwd, workspace_root: None,
        )

        monkeypatch.setattr(
            "coding_agent.tools.shell._load_sandbox_module",
            lambda: fake_module,
        )

        pipeline_ctx = SimpleNamespace(
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "docker"},
            }
        )

        result = bash_run(
            command="echo ok",
            cwd=str(workspace),
            env={"CALLER_ONLY": "explicit"},
            __pipeline_ctx__=pipeline_ctx,
        )

        assert _as_text(result) == "ok"
        request = cast(dict[str, object], captured["request"])
        env = cast(dict[str, str], request["env"])
        assert env == {"CALLER_ONLY": "explicit"}
        assert "HOST_ONLY" not in env

    def test_sandbox_module_imports_without_resource_module(self, monkeypatch):
        module_name = "coding_agent.tools.sandbox"
        sys.modules.pop(module_name, None)

        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "resource":
                raise ModuleNotFoundError("No module named 'resource'")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        module = importlib.import_module(module_name)
        assert hasattr(module, "build_sandbox")
        sys.modules[module_name] = sandbox_module

    def test_docker_sandbox_forwards_explicit_env_only(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = sandbox_module.SandboxConfig(
            mode="docker",
            workspace_root=workspace,
            limits=sandbox_module.SandboxLimits(),
        )
        runner = sandbox_module.DockerSandboxRunner(config)
        request = sandbox_module.SandboxRequest(
            args=["python", "-V"],
            cwd=workspace,
            env={"SAFE_VAR": "ok"},
            timeout_seconds=1,
        )

        monkeypatch.setattr(sandbox_module, "which", lambda _: "/usr/bin/docker")
        captured_command: list[str] = []
        captured_env: dict[str, str] | None = None

        def fake_run(command, **kwargs):
            nonlocal captured_command, captured_env
            captured_command = command
            captured_env = kwargs.get("env")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
        result = runner.run(request)

        assert result.returncode == 0
        assert captured_env is None
        assert "-e" in captured_command
        assert "SAFE_VAR=ok" in captured_command

    def test_docker_sandbox_rejects_unsafe_env_names(self, monkeypatch, tmp_path: Path):
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = sandbox_module.SandboxConfig(
            mode="docker",
            workspace_root=workspace,
            limits=sandbox_module.SandboxLimits(),
        )
        runner = sandbox_module.DockerSandboxRunner(config)
        request = sandbox_module.SandboxRequest(
            args=["python", "-V"],
            cwd=workspace,
            env={"BAD NAME": "oops"},
            timeout_seconds=1,
        )

        monkeypatch.setattr(sandbox_module, "which", lambda _: "/usr/bin/docker")
        monkeypatch.setattr(
            sandbox_module.subprocess,
            "run",
            lambda *args, **kwargs: pytest.fail("subprocess.run should not be called"),
        )

        with pytest.raises(
            sandbox_module.SandboxError, match="(?i)unsafe environment variable name"
        ):
            runner.run(request)
