import pytest
from coding_agent.tools.shell import bash_run


class TestShellTool:
    def test_basic_command(self):
        result = bash_run(command="echo hello")
        assert "hello" in result

    def test_stderr_captured(self):
        result = bash_run(command="echo err >&2")
        assert "err" in result

    def test_exit_code_shown(self):
        result = bash_run(command="exit 1")
        assert "Exit code: 1" in result
