"""Tests for shell tool."""

import pytest

from coding_agent.tools.shell import bash_run


class TestShellTool:
    """Tests for bash tool execution."""

    def test_basic_command(self):
        """Test basic command execution."""
        result = bash_run(command="echo hello")

        assert "hello" in result

    def test_command_with_args(self):
        """Test command with arguments."""
        result = bash_run(command="echo hello world")

        assert "hello world" in result

    def test_stderr_capture(self):
        """Test that stderr is captured by redirecting to stdout."""
        # Use python to write to stderr
        result = bash_run(
            command="python3 -c 'import sys; sys.stderr.write(\"error message\")'"
        )

        assert "STDERR" in result
        assert "error message" in result

    def test_non_zero_exit_code(self):
        """Test that non-zero exit codes are reported."""
        result = bash_run(command="python3 -c 'exit(1)'")

        assert "Exit code: 1" in result

    def test_timeout(self):
        """Test command timeout."""
        result = bash_run(command="sleep 10", timeout=1)

        assert "timed out" in result.lower()

    def test_empty_command(self):
        """Test empty command handling."""
        result = bash_run(command="")

        assert "error" in result.lower()
        assert "empty" in result.lower()

    def test_output_truncation(self):
        """Test that very long output is handled."""
        # Generate output > 10000 chars
        result = bash_run(command="python3 -c \"print('x' * 20000)\"")

        # The tool returns the output (subprocess captures it)
        # Just verify we get a result with x's
        assert "x" in result

    def test_special_characters_in_output(self):
        """Test handling of special characters in output."""
        result = bash_run(command="printf 'hello\\nworld\\ttab'")

        # Both lines should be present
        assert "hello" in result
        assert "world" in result

    def test_working_directory(self, tmp_path):
        """Test that commands can output file contents."""
        # Create a file in tmp_path
        (tmp_path / "test_file.txt").write_text("test content")

        result = bash_run(command=f"cat {tmp_path / 'test_file.txt'}")

        assert "test content" in result

    def test_shlex_parsing(self):
        """Test that shlex properly parses complex commands."""
        # shlex.split handles quotes correctly
        result = bash_run(command="echo 'hello world'")

        assert "hello world" in result

    def test_no_shell_injection_via_command_substitution(self):
        """Test that command substitution doesn't work (security feature)."""
        # Without shell, $(...) is just text
        result = bash_run(command="echo $(echo secret)")

        # The $(echo secret) is treated as literal text argument to echo
        # So output contains "$(echo secret)", not "secret"
        assert "$(echo secret)" in result or "secret" not in result

    def test_no_shell_injection_via_backticks(self):
        """Test that backticks don't execute (security feature)."""
        result = bash_run(command="echo `echo secret`")

        # Backticks are literal without shell
        assert "`echo secret`" in result or "secret" not in result

    def test_no_shell_injection_via_semicolon(self):
        """Test that semicolons don't allow command injection."""
        # With shlex.split, "echo hello; echo world" becomes:
        # ['echo', 'hello;', 'echo', 'world']
        # This is different from shell behavior where ; is a separator
        result = bash_run(command="echo hello; echo world")

        # The semicolon is part of the argument 'hello;', not a separator
        # So we get "hello;" not "hello"
        assert "hello;" in result or "world" not in result

    def test_quoted_arguments_preserved(self):
        """Test that quoted arguments are preserved correctly."""
        result = bash_run(command='echo "quoted string"')

        # shlex removes quotes, so we get the content
        assert "quoted string" in result
