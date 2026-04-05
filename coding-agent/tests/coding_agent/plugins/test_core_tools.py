import pytest
from coding_agent.plugins.core_tools import CoreToolsPlugin
from coding_agent.plugins.shell_session import ShellSessionPlugin
from agentkit.tools.schema import ToolSchema
from coding_agent.core.planner import PlanManager
import json


class TestCoreToolsPlugin:
    def test_state_key(self):
        plugin = CoreToolsPlugin()
        assert plugin.state_key == "core_tools"

    def test_hooks_include_get_tools(self):
        plugin = CoreToolsPlugin()
        hooks = plugin.hooks()
        assert "get_tools" in hooks

    def test_hooks_include_execute_tool(self):
        plugin = CoreToolsPlugin()
        hooks = plugin.hooks()
        assert "execute_tool" in hooks

    def test_get_tools_returns_schemas(self):
        plugin = CoreToolsPlugin()
        schemas = plugin.get_tools()
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert all(isinstance(s, ToolSchema) for s in schemas)

    def test_execute_tool_runs_tool(self, tmp_path):
        plugin = CoreToolsPlugin(workspace_root=tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("test content")
        result = plugin.execute_tool(name="file_read", arguments={"path": "test.txt"})
        assert "test content" in result

    def test_execute_tool_blocks_paths_outside_workspace(self, tmp_path):
        plugin = CoreToolsPlugin(workspace_root=tmp_path)
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("secret")

        result = plugin.execute_tool(name="file_read", arguments={"path": str(outside)})

        assert "outside workspace" in result.lower()

    def test_planner_state_is_instance_scoped(self, tmp_path):
        plugin_one = CoreToolsPlugin(workspace_root=tmp_path, planner=PlanManager())
        plugin_two = CoreToolsPlugin(workspace_root=tmp_path, planner=PlanManager())

        result = plugin_one.execute_tool(
            name="todo_write",
            arguments={"tasks": [{"title": "Task A", "status": "todo"}]},
        )
        assert "updated 1 todos" in result.lower() or "status" in result.lower()

        plugin_two_read = plugin_two.execute_tool(name="todo_read", arguments={})
        assert "no tasks" in plugin_two_read.lower()

    def test_includes_file_tools(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "file_read" in names
        assert "file_write" in names

    def test_includes_shell_tool(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "bash_run" in names

    def test_includes_search_tools(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "grep_search" in names
        assert "glob_files" in names

    def test_includes_web_search_tool(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "web_search" in names

    def test_execute_web_search_uses_backend(self, tmp_path):
        class RecordingBackend:
            def __init__(self) -> None:
                self.calls = []

            def search(self, query: str, limit: int):
                self.calls.append((query, limit))
                return [
                    {
                        "title": "Result",
                        "url": "https://example.com",
                        "snippet": "Snippet",
                    }
                ]

        backend = RecordingBackend()
        plugin = CoreToolsPlugin(workspace_root=tmp_path, web_search_backend=backend)

        result = plugin.execute_tool(
            name="web_search",
            arguments={"query": "agent", "limit": 2},
        )
        payload = json.loads(result)

        assert backend.calls == [("agent", 2)]
        assert payload["results"][0]["url"] == "https://example.com"

    def test_execute_web_search_without_backend_uses_default_backend(self, tmp_path):
        plugin = CoreToolsPlugin(workspace_root=tmp_path)

        result = plugin.execute_tool(
            name="web_search",
            arguments={"query": "agent", "limit": 1},
        )
        payload = json.loads(result)

        assert payload["results"][0]["title"] == "Mock result for agent"

    def test_bash_run_uses_and_updates_shell_session(self, tmp_path):
        shell_session = ShellSessionPlugin()
        shell_session.do_mount()
        shell_session.update_cwd(str(tmp_path))

        subdir = tmp_path / "subdir"
        subdir.mkdir()

        plugin = CoreToolsPlugin(workspace_root=tmp_path, shell_session=shell_session)

        cd_result = plugin.execute_tool(
            name="bash_run",
            arguments={"command": "cd subdir"},
        )
        assert "changed directory" in cd_result.lower()
        assert shell_session.get_session_context()["cwd"] == str(subdir.resolve())

        export_result = plugin.execute_tool(
            name="bash_run",
            arguments={"command": "export TEST_VALUE=session-ok"},
        )
        assert "test_value" in export_result.lower()

        result = plugin.execute_tool(
            name="bash_run",
            arguments={
                "command": "python3 -c \"import os; from pathlib import Path; print(Path.cwd().name); print(os.environ.get('TEST_VALUE', 'missing'))\""
            },
        )
        assert "subdir" in result
        assert "session-ok" in result

    def test_export_with_spaces_updates_shell_session_from_result(self, tmp_path):
        shell_session = ShellSessionPlugin()
        shell_session.do_mount()
        plugin = CoreToolsPlugin(workspace_root=tmp_path, shell_session=shell_session)

        result = plugin.execute_tool(
            name="bash_run",
            arguments={"command": 'export TEST_VALUE="two words"'},
        )

        assert result == "Exported TEST_VALUE=two words"
        assert (
            shell_session.get_session_context()["env_vars"]["TEST_VALUE"] == "two words"
        )

    def test_failed_export_does_not_update_shell_session(self, tmp_path):
        shell_session = ShellSessionPlugin()
        shell_session.do_mount()
        plugin = CoreToolsPlugin(workspace_root=tmp_path, shell_session=shell_session)

        result = plugin.execute_tool(
            name="bash_run",
            arguments={"command": "export TEST_VALUE=value && echo nope"},
        )

        assert result.lower().startswith("error:")
        assert "TEST_VALUE" not in shell_session.get_session_context()["env_vars"]
