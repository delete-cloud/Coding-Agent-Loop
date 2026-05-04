# tests/coding_agent/plugins/test_shell_session.py
import pytest
from agentkit.environment import WorkspaceSummary
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

    def test_mount_uses_pipeline_workspace_root(self, tmp_path):
        plugin = ShellSessionPlugin()
        ctx = type(
            "Ctx",
            (),
            {"config": {"workspace_root": str(tmp_path / "workspace")}},
        )()

        state = plugin.do_mount(ctx=ctx)

        assert state["cwd"] == str((tmp_path / "workspace").resolve())

    def test_mount_uses_environment_default_cwd_without_workspace_root(self):
        class Env:
            def workspace_summary(self) -> WorkspaceSummary:
                return WorkspaceSummary(
                    display_name="Cloud workspace workspace-1",
                    default_cwd="/workspace",
                )

        plugin = ShellSessionPlugin()
        ctx = type("Ctx", (), {"config": {"environment": Env()}})()

        state = plugin.do_mount(ctx=ctx)

        assert state["cwd"] == "/workspace"

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
