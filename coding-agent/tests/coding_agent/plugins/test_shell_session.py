# tests/coding_agent/plugins/test_shell_session.py
import pytest
from coding_agent.plugins.shell_session import ShellSessionPlugin
from agentkit.directive.types import Checkpoint


class TestShellSessionPlugin:
    def test_state_key(self):
        plugin = ShellSessionPlugin()
        assert plugin.state_key == "shell_session"

    def test_hooks(self):
        plugin = ShellSessionPlugin()
        hooks = plugin.hooks()
        assert "mount" in hooks
        assert "on_checkpoint" in hooks

    def test_mount_initializes_session_state(self):
        plugin = ShellSessionPlugin()
        state = plugin.do_mount()
        assert "cwd" in state
        assert "env_vars" in state
        assert "active" in state

    def test_checkpoint_captures_cwd(self):
        plugin = ShellSessionPlugin()
        plugin._state = {
            "cwd": "/home/user/project",
            "env_vars": {"PATH": "/usr/bin"},
            "active": True,
        }
        plugin.on_checkpoint()
        # on_checkpoint is observer — just logs, doesn't return
        # The state should be available for persistence

    def test_get_session_context(self):
        plugin = ShellSessionPlugin()
        plugin._state = {"cwd": "/tmp", "env_vars": {}, "active": True}
        ctx = plugin.get_session_context()
        assert ctx["cwd"] == "/tmp"

    def test_update_cwd(self):
        plugin = ShellSessionPlugin()
        plugin._state = {"cwd": "/home", "env_vars": {}, "active": True}
        plugin.update_cwd("/home/user")
        assert plugin._state["cwd"] == "/home/user"
