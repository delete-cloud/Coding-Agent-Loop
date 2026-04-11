import pytest
from coding_agent.tools.shell import bash_run


class TestShellTool:
    def test_basic_command(self):
        result = bash_run(command="echo hello")
        assert "hello" in result

    def test_stderr_captured(self):
        result = bash_run(command="python -c \"import sys; sys.stderr.write('err')\"")
        assert "err" in result

    def test_exit_code_shown(self):
        result = bash_run(command='python -c "import sys; sys.exit(1)"')
        assert "Exit code: 1" in result

    def test_rejects_shell_metacharacters(self):
        result = bash_run(command="echo hello && rm -rf /tmp/nope")
        assert isinstance(result, str)
        assert "unsupported shell syntax" in result.lower()
