"""Tests asserting CoreToolsPlugin registers all expected tools including file_patch and subagent."""

from __future__ import annotations

import json
import textwrap

import pytest

from coding_agent.plugins.core_tools import CoreToolsPlugin


# ── Tool Registration ──────────────────────────────────────────────

EXPECTED_TOOLS = {
    "file_read",
    "file_write",
    "file_replace",
    "glob_files",
    "grep_search",
    "bash_run",
    "todo_write",
    "todo_read",
    "file_patch",
    "subagent",
}


class TestToolRegistration:
    """CoreToolsPlugin must expose all 10 expected tools."""

    def test_registers_all_expected_tools(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert EXPECTED_TOOLS.issubset(names), (
            f"Missing tools: {EXPECTED_TOOLS - names}"
        )

    def test_total_tool_count_is_at_least_10(self):
        plugin = CoreToolsPlugin()
        schemas = plugin.get_tools()
        assert len(schemas) >= 10

    def test_file_patch_registered(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "file_patch" in names

    def test_subagent_registered(self):
        plugin = CoreToolsPlugin()
        names = {s.name for s in plugin.get_tools()}
        assert "subagent" in names


# ── file_patch Functional Tests ────────────────────────────────────


class TestFilePatchTool:
    """file_patch should apply unified diffs to files."""

    def test_applies_simple_patch(self, tmp_path):
        plugin = CoreToolsPlugin(workspace_root=tmp_path)

        # Create a file to patch
        target = tmp_path / "hello.py"
        target.write_text("def greet():\n    return 'hello'\n")

        patch = textwrap.dedent("""\
            @@ -1,2 +1,2 @@
             def greet():
            -    return 'hello'
            +    return 'hello world'
        """)

        result = plugin.execute_tool(
            name="file_patch",
            arguments={"path": "hello.py", "patch": patch},
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["changed"] is True

        # Verify file was actually changed
        assert "hello world" in target.read_text()

    def test_patch_nonexistent_file_returns_error(self, tmp_path):
        plugin = CoreToolsPlugin(workspace_root=tmp_path)

        patch = "@@ -1,1 +1,1 @@\n-old\n+new\n"
        result = plugin.execute_tool(
            name="file_patch",
            arguments={"path": "missing.txt", "patch": patch},
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_patch_bad_context_returns_error(self, tmp_path):
        plugin = CoreToolsPlugin(workspace_root=tmp_path)

        target = tmp_path / "x.txt"
        target.write_text("aaa\nbbb\n")

        patch = textwrap.dedent("""\
            @@ -1,2 +1,2 @@
             zzz
            -bbb
            +ccc
        """)

        result = plugin.execute_tool(
            name="file_patch",
            arguments={"path": "x.txt", "patch": patch},
        )
        data = json.loads(result)
        assert data["success"] is False


# ── subagent Stub Tests ────────────────────────────────────────────


class TestSubagentTool:
    """subagent must be registered and currently raises NotImplementedError."""

    def test_subagent_raises_not_implemented(self):
        plugin = CoreToolsPlugin()
        with pytest.raises(NotImplementedError, match="not yet wired"):
            plugin.execute_tool(
                name="subagent",
                arguments={"goal": "test task"},
            )

    def test_subagent_schema_has_goal_parameter(self):
        plugin = CoreToolsPlugin()
        schemas = {s.name: s for s in plugin.get_tools()}
        subagent_schema = schemas["subagent"]
        params = subagent_schema.parameters
        assert "goal" in params["properties"]
        assert "goal" in params.get("required", [])


# ── Existing Tools Still Work ──────────────────────────────────────


class TestExistingToolsStillWork:
    """Adding new tools must not break existing ones."""

    def test_file_read_works(self, tmp_path):
        plugin = CoreToolsPlugin(workspace_root=tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("existing content")
        result = plugin.execute_tool(name="file_read", arguments={"path": "test.txt"})
        assert "existing content" in result

    def test_bash_run_works(self):
        plugin = CoreToolsPlugin()
        result = plugin.execute_tool(
            name="bash_run", arguments={"command": "echo hello"}
        )
        assert "hello" in result
