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

    def test_timeout_numeric_string_is_normalized(self):
        result = bash_run(command="echo hello", timeout="1")  # type: ignore[arg-type]
        assert _as_text(result) == "hello"

    def test_invalid_timeout_string_returns_clear_error(self):
        result = bash_run(command="echo hello", timeout="soon")  # type: ignore[arg-type]
        result_text = _as_text(result).lower()
        assert "error" in result_text
        assert "timeout" in result_text
        assert "positive integer" in result_text

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
        result_text = _as_text(result).lower()
        assert "outside sandbox workspace" in result_text
        assert str(workspace).lower() in result_text

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
        assert str(workspace).lower() in blocked_text
        assert "secret" not in blocked_text

    def test_none_sandbox_allows_relative_paths_with_slashes(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        pipeline_ctx = SimpleNamespace(
            config={"workspace_root": str(workspace), "shell": {"sandbox_mode": "none"}}
        )

        mkdir_result = bash_run(
            command="mkdir -p src/mini_strings tests",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )
        pytest_result = bash_run(
            command="python3 -m pytest tests/test_slug.py -q",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )

        assert _as_text(mkdir_result) == "(no output)"
        assert "outside sandbox workspace" not in _as_text(pytest_result).lower()

    def test_cd_outside_workspace_is_rejected(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        pipeline_ctx = SimpleNamespace(
            config={"workspace_root": str(workspace), "shell": {"sandbox_mode": "none"}}
        )
        result = bash_run(
            command="cd /tmp", cwd=str(workspace), __pipeline_ctx__=pipeline_ctx
        )
        result_text = _as_text(result).lower()
        assert "outside sandbox workspace" in result_text
        assert str(workspace).lower() in result_text

    def test_tool_description_guides_simple_workspace_safe_commands(self):
        description = bash_run._tool_schema.description
        assert "separate bash_run calls" in description
        assert "workspace root" in description
        assert "&&" in description

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
            build_sandbox=lambda config: FakeSandbox(),
            SandboxLimits=lambda **kwargs: SimpleNamespace(**kwargs),
            SandboxConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            _validate_cwd=lambda cwd, workspace_root: None,
        )
        monkeypatch.setenv("HOST_ONLY", "host-value")
        monkeypatch.setattr(
            "coding_agent.tools.shell._load_sandbox_module", lambda: fake_module
        )

        result = bash_run(
            command="echo hello",
            env={"EXPLICIT_ONLY": "1"},
            __pipeline_ctx__=SimpleNamespace(
                config={"shell": {"sandbox_mode": "docker"}}
            ),
        )
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


class TestNativeSandboxResolution:
    """ADR-0060: `native` resolves to a platform-specific backend."""

    def _native_config(self, tmp_path: Path):
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return sandbox_module, sandbox_module.SandboxConfig(
            mode="native",
            workspace_root=workspace,
            limits=sandbox_module.SandboxLimits(),
        )

    def test_native_mode_on_darwin_selects_macos_seatbelt_runner(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module, config = self._native_config(tmp_path)
        monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Darwin")
        runner = sandbox_module.build_sandbox(config)
        assert isinstance(runner, sandbox_module.MacosSeatbeltSandboxRunner)

    def test_native_mode_on_linux_selects_linux_bwrap_runner(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module, config = self._native_config(tmp_path)
        monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Linux")
        runner = sandbox_module.build_sandbox(config)
        assert isinstance(runner, sandbox_module.LinuxNativeSandboxRunner)

    def test_native_mode_on_windows_fails_closed(self, monkeypatch, tmp_path: Path):
        sandbox_module, config = self._native_config(tmp_path)
        monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Windows")
        with pytest.raises(
            sandbox_module.SandboxUnavailableError, match="(?i)native.*not supported"
        ):
            sandbox_module.build_sandbox(config)

    def test_nsjail_mode_is_rejected_by_validation(self):
        from coding_agent.tools.shell import _sandbox_mode

        with pytest.raises(ValueError, match="(?i)unsupported sandbox mode"):
            _sandbox_mode({"sandbox_mode": "nsjail"})

    def test_existing_none_and_docker_modes_unchanged(self, tmp_path: Path):
        from coding_agent.tools.shell import _sandbox_mode

        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        assert _sandbox_mode({"sandbox_mode": "none"}) == "none"
        assert _sandbox_mode({"sandbox_mode": "docker"}) == "docker"

        none_runner = sandbox_module.build_sandbox(
            sandbox_module.SandboxConfig(mode="none", workspace_root=workspace)
        )
        docker_runner = sandbox_module.build_sandbox(
            sandbox_module.SandboxConfig(mode="docker", workspace_root=workspace)
        )
        assert isinstance(none_runner, sandbox_module.NoneSandboxRunner)
        assert isinstance(docker_runner, sandbox_module.DockerSandboxRunner)


class TestMacosSeatbeltSandbox:
    """ADR-0060 step 2: macOS native sandbox via sandbox-exec."""

    def _runner(self, tmp_path: Path):
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = sandbox_module.SandboxConfig(
            mode="native",
            workspace_root=workspace,
            limits=sandbox_module.SandboxLimits(),
        )
        return sandbox_module, workspace, sandbox_module.MacosSeatbeltSandboxRunner(
            config
        )

    def test_macos_native_builds_sandbox_exec_profile_with_workspace_rw_and_network_deny(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module, workspace, runner = self._runner(tmp_path)
        monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(
            sandbox_module, "which", lambda _: "/usr/bin/sandbox-exec"
        )

        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
        result = runner.run(
            sandbox_module.SandboxRequest(
                args=["echo", "hi"], cwd=workspace, env=None, timeout_seconds=1
            )
        )

        assert result.returncode == 0
        command = cast(list[str], captured["command"])
        assert command[0] == "sandbox-exec"
        assert command[-2:] == ["echo", "hi"]
        # cwd is enforced inside the workspace
        assert captured["cwd"] == str(workspace.resolve())
        profile = command[command.index("-p") + 1]
        assert "(deny default)" in profile
        assert "(allow file-read*)" in profile
        assert f'(subpath "{workspace.resolve()}")' in profile
        # network is denied: no network-allow rule is emitted
        assert "allow network" not in profile

    def test_macos_native_fails_closed_when_sandbox_exec_missing(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module, workspace, runner = self._runner(tmp_path)
        monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(sandbox_module, "which", lambda _: None)
        monkeypatch.setattr(
            sandbox_module.subprocess,
            "run",
            lambda *a, **k: pytest.fail("subprocess.run should not be called"),
        )
        with pytest.raises(
            sandbox_module.SandboxUnavailableError, match="(?i)sandbox-exec"
        ):
            runner.run(
                sandbox_module.SandboxRequest(
                    args=["echo", "hi"], cwd=workspace, env=None, timeout_seconds=1
                )
            )


class TestLinuxNativeSandbox:
    """ADR-0060 step 3: Linux native sandbox via bubblewrap."""

    def _runner(self, tmp_path: Path):
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = sandbox_module.SandboxConfig(
            mode="native",
            workspace_root=workspace,
            limits=sandbox_module.SandboxLimits(),
        )
        return sandbox_module, workspace, sandbox_module.LinuxNativeSandboxRunner(config)

    def test_linux_native_command_includes_network_off_workspace_bind_and_cwd(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module, workspace, runner = self._runner(tmp_path)
        monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Linux")
        monkeypatch.setattr(sandbox_module, "which", lambda _: "/usr/bin/bwrap")

        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
        result = runner.run(
            sandbox_module.SandboxRequest(
                args=["echo", "hi"], cwd=workspace, env=None, timeout_seconds=1
            )
        )

        assert result.returncode == 0
        command = cast(list[str], captured["command"])
        assert command[0] == "bwrap"
        assert "--unshare-net" in command
        root = str(workspace.resolve())
        bind_idx = command.index("--bind")
        assert command[bind_idx + 1] == root
        assert command[bind_idx + 2] == root
        chdir_idx = command.index("--chdir")
        assert command[chdir_idx + 1] == root
        assert command[-2:] == ["echo", "hi"]

    def test_linux_native_fails_closed_when_bwrap_missing(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module, workspace, runner = self._runner(tmp_path)
        monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Linux")
        monkeypatch.setattr(sandbox_module, "which", lambda _: None)
        monkeypatch.setattr(
            sandbox_module.subprocess,
            "run",
            lambda *a, **k: pytest.fail("subprocess.run should not be called"),
        )
        with pytest.raises(
            sandbox_module.SandboxUnavailableError, match="(?i)bubblewrap|bwrap"
        ):
            runner.run(
                sandbox_module.SandboxRequest(
                    args=["echo", "hi"], cwd=workspace, env=None, timeout_seconds=1
                )
            )


class TestPodmanSandbox:
    """ADR-0060 step 4: explicit podman container backend."""

    def test_podman_mode_is_accepted_by_validation(self):
        from coding_agent.tools.shell import _sandbox_mode

        assert _sandbox_mode({"sandbox_mode": "podman"}) == "podman"

    def test_podman_runner_builds_podman_run_with_network_none_and_workspace_bind(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        config = sandbox_module.SandboxConfig(
            mode="podman",
            workspace_root=workspace,
            limits=sandbox_module.SandboxLimits(),
        )
        runner = sandbox_module.build_sandbox(config)
        assert isinstance(runner, sandbox_module.PodmanSandboxRunner)

        monkeypatch.setattr(sandbox_module, "which", lambda _: "/usr/bin/podman")
        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
        result = runner.run(
            sandbox_module.SandboxRequest(
                args=["echo", "hi"], cwd=workspace, env=None, timeout_seconds=1
            )
        )

        assert result.returncode == 0
        command = cast(list[str], captured["command"])
        assert command[:3] == ["podman", "run", "--rm"]
        assert "--network" in command
        assert command[command.index("--network") + 1] == "none"
        root = str(workspace.resolve())
        assert any(
            f"type=bind,src={root},dst={root}" in part for part in command
        )
        assert command[-2:] == ["echo", "hi"]

    def test_podman_runner_fails_closed_when_binary_missing(
        self, monkeypatch, tmp_path: Path
    ):
        sandbox_module = importlib.import_module("coding_agent.tools.sandbox")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runner = sandbox_module.PodmanSandboxRunner(
            sandbox_module.SandboxConfig(mode="podman", workspace_root=workspace)
        )
        monkeypatch.setattr(sandbox_module, "which", lambda _: None)
        with pytest.raises(
            sandbox_module.SandboxUnavailableError, match="(?i)podman"
        ):
            runner.run(
                sandbox_module.SandboxRequest(
                    args=["echo", "hi"], cwd=workspace, env=None, timeout_seconds=1
                )
            )
