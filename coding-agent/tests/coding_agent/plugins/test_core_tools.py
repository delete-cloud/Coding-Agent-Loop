import pytest
from coding_agent.plugins.core_tools import CoreToolsPlugin
from agentkit.tools.schema import ToolSchema
from coding_agent.core.planner import PlanManager


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
