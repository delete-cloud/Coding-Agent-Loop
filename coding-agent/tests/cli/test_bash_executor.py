"""Tests for inline bash executor (! mode)."""

from io import StringIO

import pytest
from prompt_toolkit.output.defaults import create_output

from coding_agent.cli.bash_executor import BashExecutor


class TestBashExecutor:
    def _make_executor(self) -> tuple[BashExecutor, StringIO]:
        buf = StringIO()
        executor = BashExecutor(output=create_output(stdout=buf))
        return executor, buf

    @pytest.mark.asyncio
    async def test_execute_simple_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("echo hello")
        output = buf.getvalue()
        assert "hello" in output
        assert "\x1b[" not in output
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_failing_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("false")
        assert exit_code != 0

    @pytest.mark.asyncio
    async def test_execute_shows_exit_code_on_failure(self):
        executor, buf = self._make_executor()
        await executor.execute("false")
        output = buf.getvalue()
        assert "exit" in output.lower() or "1" in output

    @pytest.mark.asyncio
    async def test_execute_multiword_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("echo foo bar baz")
        output = buf.getvalue()
        assert "foo bar baz" in output
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_empty_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("")
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_whitespace_only(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("   ")
        assert exit_code == 0

    def test_is_bash_command(self):
        from coding_agent.cli.bash_executor import is_bash_command

        assert is_bash_command("!ls") is True
        assert is_bash_command("! ls") is True
        assert is_bash_command("!  git status") is True
        assert is_bash_command("!") is False
        assert is_bash_command("hello") is False
        assert is_bash_command("/help") is False
        assert is_bash_command("") is False

    def test_extract_bash_command(self):
        from coding_agent.cli.bash_executor import extract_bash_command

        assert extract_bash_command("!ls") == "ls"
        assert extract_bash_command("! ls -la") == "ls -la"
        assert extract_bash_command("!  git status") == "git status"
