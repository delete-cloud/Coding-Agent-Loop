"""Tests for shell tool."""

import json
import pytest

from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.shell import register_shell_tools


class TestShellTool:
    """Tests for bash tool execution."""

    @pytest.fixture
    def registry(self, tmp_path):
        """Create a tool registry with shell tools."""
        reg = ToolRegistry()
        register_shell_tools(reg, cwd=tmp_path)
        return reg

    @pytest.mark.asyncio
    async def test_basic_command(self, registry):
        """Test basic command execution."""
        result = await registry.execute("bash", {"command": "echo hello"})
        
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert "hello" in parsed["output"]

    @pytest.mark.asyncio
    async def test_command_with_args(self, registry):
        """Test command with arguments."""
        result = await registry.execute("bash", {"command": "echo hello world"})
        
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert "hello world" in parsed["output"]

    @pytest.mark.asyncio
    async def test_stderr_capture(self, registry):
        """Test that stderr is captured by redirecting to stdout."""
        # Use python to write to stderr
        result = await registry.execute(
            "bash", 
            {"command": "python3 -c 'import sys; sys.stderr.write(\"error message\")'"}
        )
        
        parsed = json.loads(result)
        assert "[stderr]" in parsed["output"]
        assert "error message" in parsed["output"]

    @pytest.mark.asyncio
    async def test_non_zero_exit_code(self, registry):
        """Test that non-zero exit codes are reported."""
        result = await registry.execute("bash", {"command": "python3 -c 'exit(1)'"})
        
        parsed = json.loads(result)
        assert parsed["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_timeout(self, registry):
        """Test command timeout."""
        result = await registry.execute(
            "bash", 
            {"command": "sleep 10", "timeout": 1}
        )
        
        parsed = json.loads(result)
        assert "error" in parsed
        assert "timed out" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_empty_command(self, registry):
        """Test empty command handling."""
        result = await registry.execute("bash", {"command": ""})
        
        parsed = json.loads(result)
        assert "error" in parsed
        assert "empty" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_output_truncation(self, registry):
        """Test that very long output is truncated."""
        # Generate output > 10000 chars
        result = await registry.execute(
            "bash",
            {"command": "python3 -c \"print('x' * 20000)\""}
        )
        
        parsed = json.loads(result)
        # Output should be truncated
        assert len(parsed["output"]) < 15000
        assert "more chars" in parsed["output"]

    @pytest.mark.asyncio
    async def test_special_characters_in_output(self, registry):
        """Test handling of special characters in output."""
        result = await registry.execute(
            "bash",
            {"command": "printf 'hello\\nworld\\ttab'"}
        )
        
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        # Both lines should be present
        assert "hello" in parsed["output"]
        assert "world" in parsed["output"]

    @pytest.mark.asyncio
    async def test_working_directory(self, registry, tmp_path):
        """Test that commands run in the correct directory."""
        # Create a file in tmp_path
        (tmp_path / "test_file.txt").write_text("test content")
        
        result = await registry.execute("bash", {"command": "cat test_file.txt"})
        
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert "test content" in parsed["output"]

    @pytest.mark.asyncio
    async def test_shlex_parsing(self, registry):
        """Test that shlex properly parses complex commands."""
        # shlex.split handles quotes correctly
        result = await registry.execute(
            "bash",
            {"command": "echo 'hello world'"}
        )
        
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert "hello world" in parsed["output"]

    @pytest.mark.asyncio
    async def test_no_shell_injection_via_command_substitution(self, registry):
        """Test that command substitution doesn't work (security feature)."""
        # Without shell, $(...) is just text
        result = await registry.execute(
            "bash",
            {"command": "echo $(echo secret)"}
        )
        
        parsed = json.loads(result)
        # The $(echo secret) is treated as literal text argument to echo
        # So output contains "$(echo secret)", not "secret"
        assert "$(echo secret)" in parsed["output"] or "secret" not in parsed["output"]

    @pytest.mark.asyncio
    async def test_no_shell_injection_via_backticks(self, registry):
        """Test that backticks don't execute (security feature)."""
        result = await registry.execute(
            "bash",
            {"command": "echo `echo secret`"}
        )
        
        parsed = json.loads(result)
        # Backticks are literal without shell
        assert "`echo secret`" in parsed["output"] or "secret" not in parsed["output"]

    @pytest.mark.asyncio
    async def test_no_shell_injection_via_semicolon(self, registry):
        """Test that semicolons don't allow command injection."""
        # With shlex.split, "echo hello; echo world" becomes:
        # ['echo', 'hello;', 'echo', 'world']
        # This is different from shell behavior where ; is a separator
        result = await registry.execute(
            "bash",
            {"command": "echo hello; echo world"}
        )
        
        parsed = json.loads(result)
        # The semicolon is part of the argument 'hello;', not a separator
        # So we get "hello;" not "hello"
        assert "hello;" in parsed["output"] or "world" not in parsed["output"]

    @pytest.mark.asyncio
    async def test_quoted_arguments_preserved(self, registry):
        """Test that quoted arguments are preserved correctly."""
        result = await registry.execute(
            "bash",
            {"command": "echo \"quoted string\""}
        )
        
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        # shlex removes quotes, so we get the content
        assert "quoted string" in parsed["output"]
