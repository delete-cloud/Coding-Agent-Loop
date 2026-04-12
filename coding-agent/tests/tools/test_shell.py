"""Tests for shell tool."""

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
        result = bash_run(command="python3 -c \"print('x' * 20000)\"")

        assert "x" in _as_text(result)

    def test_special_characters_in_output(self):
        """Test handling of special characters in output."""
        result = bash_run(command="printf 'hello\\nworld\\ttab'")

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
        result = bash_run(command="echo 'hello world'")

        assert "hello world" in _as_text(result)

    def test_no_shell_injection_via_command_substitution(self):
        """Test that command substitution doesn't work (security feature)."""
        result = bash_run(command="echo $(echo secret)")

        result_text = _as_text(result)
        assert "$(echo secret)" in result_text or "secret" not in result_text

    def test_no_shell_injection_via_backticks(self):
        """Test that backticks don't execute (security feature)."""
        result = bash_run(command="echo `echo secret`")

        result_text = _as_text(result)
        assert "`echo secret`" in result_text or "secret" not in result_text

    def test_no_shell_injection_via_semicolon(self):
        """Test that semicolons don't allow command injection."""
        result = bash_run(command="echo hello; echo world")

        result_text = _as_text(result)
        assert "hello;" in result_text or "world" not in result_text

    def test_quoted_arguments_preserved(self):
        """Test that quoted arguments are preserved correctly."""
        result = bash_run(command='echo "quoted string"')

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
            command=f"cd {tmp_path}",
            cwd=str(workspace),
            __pipeline_ctx__=pipeline_ctx,
        )

        assert "outside sandbox workspace" in _as_text(result).lower()
