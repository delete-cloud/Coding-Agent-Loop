import pytest
from coding_agent.plugins.core_tools import CoreToolsPlugin
from agentkit.tools.schema import ToolSchema


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
        plugin = CoreToolsPlugin()
        f = tmp_path / "test.txt"
        f.write_text("test content")
        result = plugin.execute_tool(name="file_read", arguments={"path": str(f)})
        assert "test content" in result

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
