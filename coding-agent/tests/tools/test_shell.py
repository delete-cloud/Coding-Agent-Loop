"""Tests for shell tool."""

import builtins
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

from coding_agent.tools.shell import bash_run


def _as_text(result: str | dict[str, str | int]) -> str:
    if not isinstance(result, str):
        raise TypeError(f"expected string result, got {type(result).__name__}")
    return result


class TestShellTool:
    """Tests for bash tool execution."""

    def test_basic_command(self):
        """Test basic command execution."""
        result = bash_run(command="echo hello")

        assert "hello" in _as_text(result)

    def test_command_with_args(self):
        """Test command with arguments."""
        result = bash_run(command="echo hello world")

        assert "hello world" in _as_text(result)

    def test_stderr_capture(self):
        """Test that stderr is captured by redirecting to stdout."""
        # Use python to write to stderr
        result = bash_run(
            command="python3 -c 'import sys; sys.stderr.write(\"error message\")'"
        )

        result_text = _as_text(result)
        assert "STDERR" in result_text
        assert "error message" in result_text

    def test_non_zero_exit_code(self):
        """Test that non-zero exit codes are reported."""
        result = bash_run(command="python3 -c 'exit(1)'")

        assert "Exit code: 1" in _as_text(result)

    def test_timeout(self):
        """Test command timeout."""
        result = bash_run(command="sleep 10", timeout=1)

        assert "timed out" in _as_text(result).lower()

    def test_empty_command(self):
        """Test empty command handling."""
        result = bash_run(command="")

        result_text = _as_text(result).lower()
        assert "error" in result_text
        assert "empty" in result_text

    def test_output_truncation(self):
        """Test that very long output is handled."""
        # Generate output > 10000 chars
        result = bash_run(command="python3 -c \"print('x' * 20000)\"")

        # The tool returns the output (subprocess captures it)
        # Just verify we get a result with x's
        assert "x" in _as_text(result)

    def test_special_characters_in_output(self):
        """Test handling of special characters in output."""
        result = bash_run(command="printf 'hello\\nworld\\ttab'")

        # Both lines should be present
        result_text = _as_text(result)
        assert "hello" in result_text
        assert "world" in result_text

    def test_working_directory(self, tmp_path: Path):
        """Test that commands can output file contents."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _ = (workspace / "test_file.txt").write_text("test content")

        pipeline_ctx = SimpleNamespace(
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "none"},
            }
        )

        result = bash_run(
            command=f"cat {workspace / 'test_file.txt'}",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )

        assert "test content" in _as_text(result)

    def test_shlex_parsing(self):
        """Test that shlex properly parses complex commands."""
        # shlex.split handles quotes correctly
        result = bash_run(command="echo 'hello world'")

        assert "hello world" in _as_text(result)

    def test_no_shell_injection_via_command_substitution(self):
        """Test that command substitution doesn't work (security feature)."""
        # Without shell, $(...) is just text
        result = bash_run(command="echo $(echo secret)")

        # The $(echo secret) is treated as literal text argument to echo
        # So output contains "$(echo secret)", not "secret"
        result_text = _as_text(result)
        assert "$(echo secret)" in result_text or "secret" not in result_text

    def test_no_shell_injection_via_backticks(self):
        """Test that backticks don't execute (security feature)."""
        result = bash_run(command="echo `echo secret`")

        # Backticks are literal without shell
        result_text = _as_text(result)
        assert "`echo secret`" in result_text or "secret" not in result_text

    def test_no_shell_injection_via_semicolon(self):
        """Test that semicolons don't allow command injection."""
        # With shlex.split, "echo hello; echo world" becomes:
        # ['echo', 'hello;', 'echo', 'world']
        # This is different from shell behavior where ; is a separator
        result = bash_run(command="echo hello; echo world")

        # The semicolon is part of the argument 'hello;', not a separator
        # So we get "hello;" not "hello"
        result_text = _as_text(result)
        assert "hello;" in result_text or "world" not in result_text

    def test_quoted_arguments_preserved(self):
        """Test that quoted arguments are preserved correctly."""
        result = bash_run(command='echo "quoted string"')

        # shlex removes quotes, so we get the content
        assert "quoted string" in _as_text(result)

    def test_none_sandbox_mode_executes_through_abstraction(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _ = (workspace / "message.txt").write_text("hello from sandbox")

        pipeline_ctx = SimpleNamespace(
            config={
                "workspace_root": str(workspace),
                "shell": {
                    "sandbox_mode": "none",
                    "cpu_limit_seconds": 1,
                },
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
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "none"},
            }
        )

        result = bash_run(
            command="pwd",
            cwd=str(tmp_path),
            __pipeline_ctx__=pipeline_ctx,
        )

        assert "outside sandbox workspace" in _as_text(result).lower()

    def test_none_sandbox_blocks_absolute_path_escape(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        _ = outside.write_text("secret")

        pipeline_ctx = SimpleNamespace(
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "none"},
            }
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
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "none"},
            }
        )

        result = bash_run(
            command="cd /tmp",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )

        assert "outside sandbox workspace" in _as_text(result).lower()

    def test_cd_within_workspace_succeeds(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        subdir = workspace / "sub"
        subdir.mkdir(parents=True)

        pipeline_ctx = SimpleNamespace(
            config={
                "workspace_root": str(workspace),
                "shell": {"sandbox_mode": "none"},
            }
        )

        result = bash_run(
            command="cd sub",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
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
        request = captured["request"]
        assert request["env"] == {"CALLER_ONLY": "explicit"}
        assert "HOST_ONLY" not in request["env"]

    def test_sandbox_module_imports_without_resource_module(self, monkeypatch):
        sys.modules.pop("coding_agent.tools.sandbox", None)

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "resource":
                raise ImportError("resource is unavailable")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        sandbox = importlib.import_module("coding_agent.tools.sandbox")

        assert sandbox.SandboxLimits().__class__.__name__ == "SandboxLimits"

    def test_docker_sandbox_forwards_valid_env_entries(self, tmp_path: Path):
        from coding_agent.tools.sandbox import (
            DockerSandboxRunner,
            SandboxConfig,
            SandboxLimits,
            SandboxRequest,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runner = DockerSandboxRunner(
            SandboxConfig(
                mode="docker", workspace_root=workspace, limits=SandboxLimits()
            )
        )
        request = SandboxRequest(
            args=["python", "-V"],
            cwd=workspace,
            env={
                "LANG": "C.UTF-8",
                "SAFE_VAR": "top-secret",
            },
            timeout_seconds=1,
        )

        command = runner._docker_command(request, workspace)

        assert "LANG=C.UTF-8" in command
        assert "SAFE_VAR=top-secret" in command

    def test_docker_sandbox_rejects_unsafe_env_names(self, tmp_path: Path):
        from coding_agent.tools.sandbox import (
            DockerSandboxRunner,
            SandboxConfig,
            SandboxLimits,
            SandboxRequest,
            SandboxError,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runner = DockerSandboxRunner(
            SandboxConfig(
                mode="docker", workspace_root=workspace, limits=SandboxLimits()
            )
        )
        request = SandboxRequest(
            args=["python", "-V"],
            cwd=workspace,
            env={"BAD-NAME": "nope"},
            timeout_seconds=1,
        )

        try:
            runner._docker_command(request, workspace)
        except SandboxError as exc:
            assert "BAD-NAME" in str(exc)
        else:
            raise AssertionError("expected SandboxError for unsafe env name")

    def test_docker_sandbox_uses_explicit_process_env(
        self, tmp_path: Path, monkeypatch
    ):
        from coding_agent.tools import sandbox as sandbox_module
        from coding_agent.tools.sandbox import (
            DockerSandboxRunner,
            SandboxConfig,
            SandboxLimits,
            SandboxRequest,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runner = DockerSandboxRunner(
            SandboxConfig(
                mode="docker", workspace_root=workspace, limits=SandboxLimits()
            )
        )
        request = SandboxRequest(
            args=["python", "-V"],
            cwd=workspace,
            env={"SAFE_VAR": "ok"},
            timeout_seconds=1,
        )

        captured: dict[str, object] = {}

        monkeypatch.setattr(sandbox_module.os, "environ", {"BASE": "1"})

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs["env"]
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
        monkeypatch.setattr(sandbox_module, "which", lambda name: "/usr/bin/docker")

        runner.run(request)

        assert captured["env"] == {"BASE": "1"}
