# pyright: reportMissingTypeStubs=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportAny=false, reportUnannotatedClassAttribute=false, reportPrivateUsage=false, reportUnusedCallResult=false

import json
from typing import Any

import pytest
from agentkit.environment import WorkspaceSummary
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.tools import ToolRegistry, tool
from agentkit.tools.schema import ToolSchema
from coding_agent.core.planner import PlanManager
from coding_agent.environment import LocalEnvironment
from coding_agent.plugins.core_tools import CoreToolExecutor, CoreToolsPlugin
from coding_agent.plugins.shell_session import ShellSessionPlugin
from coding_agent.tools.file_ops import (
    structured_results_scope as file_ops_structured_results_scope,
)
from coding_agent.tools.shell import (
    structured_results_scope as shell_structured_results_scope,
)


class NonLocalEnvironment:
    def __init__(self) -> None:
        self.shell_calls: list[dict[str, Any]] = []

    @property
    def kind(self) -> str:
        return "cloud"

    def tool_config(self) -> dict[str, Any]:
        return {"workspace_id": "workspace-1"}

    def workspace_summary(self) -> WorkspaceSummary:
        return WorkspaceSummary(
            display_name="Cloud workspace workspace-1",
            default_cwd="/workspace",
        )

    def build_file_tools(self):
        @tool(name="file_read", description="Read a file")
        def file_read(path: str) -> str:
            return f"cloud-read:{path}"

        @tool(name="file_write", description="Write a file")
        def file_write(path: str, content: str) -> str:
            return f"cloud-write:{path}:{content}"

        @tool(name="file_replace", description="Replace text in a file")
        def file_replace(path: str, old: str, new: str) -> str:
            return f"cloud-replace:{path}:{old}:{new}"

        @tool(name="glob_files", description="Glob files")
        def glob_files(pattern: str, directory: str = ".") -> str:
            return f"cloud-glob:{directory}:{pattern}"

        @tool(name="grep_search", description="Search files")
        def grep_search(pattern: str, directory: str = ".", include: str = "") -> str:
            return f"cloud-grep:{directory}:{pattern}:{include}"

        return file_read, file_write, file_replace, glob_files, grep_search

    def build_file_patch_tool(self):
        @tool(name="file_patch", description="Apply a patch")
        def file_patch(path: str, patch: str) -> str:
            return f"cloud-patch:{path}:{patch}"

        return file_patch

    def build_shell_tool(self):
        @tool(name="bash_run", description="Run a shell command")
        def bash_run(
            command: str,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
        ) -> str:
            self.shell_calls.append({"command": command, "cwd": cwd, "env": env})
            return f"cloud-shell:{cwd}:{command}"

        return bash_run


class TestCoreToolsPlugin:
    @staticmethod
    def _plugin() -> CoreToolsPlugin:
        executor = CoreToolExecutor()
        return CoreToolsPlugin(executor.schemas())

    def test_state_key(self):
        plugin = self._plugin()
        assert plugin.state_key == "core_tools"

    def test_hooks_expose_schemas_without_execution(self):
        plugin = self._plugin()

        assert plugin.hooks() == {"get_tools": plugin.get_tools}
        assert not hasattr(plugin, "execute_tool")
        assert not hasattr(plugin, "registry")
        assert not hasattr(plugin, "_environment")
        registry = PluginRegistry()
        registry.register(plugin)
        assert registry.get_hooks("get_tools") == [plugin.get_tools]

    def test_get_tools_returns_schemas(self):
        plugin = self._plugin()
        schemas = plugin.get_tools()
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert all(isinstance(schema, ToolSchema) for schema in schemas)

    def test_get_tools_returns_copy_of_immutable_schema_sequence(self):
        plugin = self._plugin()
        first = plugin.get_tools()
        expected_names = [schema.name for schema in first]

        first.clear()

        assert [schema.name for schema in plugin.get_tools()] == expected_names

    def test_execute_tool_runs_tool(self, tmp_path):
        plugin = CoreToolExecutor(workspace_root=tmp_path)
        f = tmp_path / "test.txt"
        f.write_text("test content")
        result = plugin.execute_tool(name="file_read", arguments={"path": "test.txt"})
        assert "test content" in result

    def test_execute_tool_uses_injected_environment(self, tmp_path):
        env = LocalEnvironment(workspace_root=tmp_path)
        plugin = CoreToolExecutor(environment=env)
        target = tmp_path / "env.txt"
        target.write_text("environment content")

        result = plugin.execute_tool(name="file_read", arguments={"path": "env.txt"})

        assert result == "environment content"

    def test_bash_run_uses_injected_environment_workspace(self, tmp_path):
        env = LocalEnvironment(workspace_root=tmp_path)
        plugin = CoreToolExecutor(environment=env)

        result = plugin.execute_tool(name="bash_run", arguments={"command": "pwd"})

        assert str(tmp_path.resolve()) in result

    def test_core_tools_accepts_non_local_environment_without_workspace_root(self):
        env = NonLocalEnvironment()
        shell_session = ShellSessionPlugin()
        shell_session._state = {
            "cwd": "/workspace",
            "env_vars": {"A": "B"},
            "active": True,
        }
        plugin = CoreToolExecutor(environment=env, shell_session=shell_session)

        read_result = plugin.execute_tool(
            name="file_read", arguments={"path": "README.md"}
        )
        shell_result = plugin.execute_tool(
            name="bash_run", arguments={"command": "pwd"}
        )

        assert plugin._workspace_root is None
        assert read_result == "cloud-read:README.md"
        assert shell_result == "cloud-shell:/workspace:pwd"
        assert env.shell_calls == [
            {"command": "pwd", "cwd": "/workspace", "env": {"A": "B"}}
        ]

    def test_execute_tool_blocks_paths_outside_workspace(self, tmp_path):
        plugin = CoreToolExecutor(workspace_root=tmp_path)
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("secret")

        result = plugin.execute_tool(name="file_read", arguments={"path": str(outside)})

        assert "outside workspace" in result.lower()

    def test_file_read_returns_structured_payload_when_enabled(self, tmp_path):
        plugin = CoreToolExecutor(workspace_root=tmp_path)
        target = tmp_path / "test.txt"
        target.write_text("line one\nline two\n")

        with file_ops_structured_results_scope(True):
            result = plugin.execute_tool(
                name="file_read", arguments={"path": "test.txt"}
            )

        assert result == {
            "content": "line one\nline two\n",
            "lines": 2,
            "path": str(target.resolve()),
        }

    def test_grep_search_returns_structured_payload_when_enabled(self, tmp_path):
        plugin = CoreToolExecutor(workspace_root=tmp_path)
        target = tmp_path / "notes.txt"
        target.write_text("TODO first\nignore\nTODO second\n")

        with file_ops_structured_results_scope(True):
            result = plugin.execute_tool(
                name="grep_search",
                arguments={"pattern": "TODO", "directory": "."},
            )

        assert result["count"] == 2
        assert len(result["matches"]) == 2
        assert result["matches"][0].endswith("notes.txt:1:TODO first")
        assert result["matches"][1].endswith("notes.txt:3:TODO second")

    def test_bash_run_returns_structured_payload_when_enabled(self, tmp_path):
        plugin = CoreToolExecutor(workspace_root=tmp_path)

        with shell_structured_results_scope(True):
            result = plugin.execute_tool(
                name="bash_run",
                arguments={
                    "command": "python3 -c \"import sys; print('out'); sys.stderr.write('err')\""
                },
            )

        assert result["stdout"] == "out\n"
        assert result["stderr"] == "err"
        assert result["exit_code"] == 0

    def test_planner_state_is_instance_scoped(self, tmp_path):
        plugin_one = CoreToolExecutor(workspace_root=tmp_path, planner=PlanManager())
        plugin_two = CoreToolExecutor(workspace_root=tmp_path, planner=PlanManager())

        result = plugin_one.execute_tool(
            name="todo_write",
            arguments={"tasks": [{"title": "Task A", "status": "todo"}]},
        )
        assert "updated 1 todos" in result.lower() or "status" in result.lower()

        plugin_two_read = plugin_two.execute_tool(name="todo_read", arguments={})
        assert "no tasks" in plugin_two_read.lower()

    def test_includes_file_tools(self):
        executor = CoreToolExecutor()
        names = {schema.name for schema in executor.schemas()}
        assert "file_read" in names
        assert "file_write" in names

    def test_includes_shell_tool(self):
        executor = CoreToolExecutor()
        names = {schema.name for schema in executor.schemas()}
        assert "bash_run" in names

    def test_includes_search_tools(self):
        executor = CoreToolExecutor()
        names = {schema.name for schema in executor.schemas()}
        assert "grep_search" in names
        assert "glob_files" in names

    def test_includes_web_search_tool(self):
        executor = CoreToolExecutor()
        names = {schema.name for schema in executor.schemas()}
        assert "web_search" in names

    def test_includes_subagent_tool(self):
        executor = CoreToolExecutor()
        names = {schema.name for schema in executor.schemas()}
        assert "subagent" in names

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
        plugin = CoreToolExecutor(workspace_root=tmp_path, web_search_backend=backend)

        result = plugin.execute_tool(
            name="web_search",
            arguments={"query": "agent", "limit": 2},
        )
        payload = json.loads(result)

        assert backend.calls == [("agent", 2)]
        assert payload["results"][0]["url"] == "https://example.com"

    def test_execute_web_search_without_backend_uses_default_backend(self, tmp_path):
        plugin = CoreToolExecutor(workspace_root=tmp_path)

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

        plugin = CoreToolExecutor(workspace_root=tmp_path, shell_session=shell_session)

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

    def test_bash_run_with_shell_session_missing_cwd_uses_workspace(self, tmp_path):
        shell_session = ShellSessionPlugin()
        shell_session._state = {"cwd": None, "env_vars": {}, "active": True}
        plugin = CoreToolExecutor(workspace_root=tmp_path, shell_session=shell_session)

        result = plugin.execute_tool(name="bash_run", arguments={"command": "pwd"})

        assert str(tmp_path.resolve()) in result

    def test_export_with_spaces_updates_shell_session_from_result(self, tmp_path):
        shell_session = ShellSessionPlugin()
        shell_session.do_mount()
        plugin = CoreToolExecutor(workspace_root=tmp_path, shell_session=shell_session)

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
        plugin = CoreToolExecutor(workspace_root=tmp_path, shell_session=shell_session)

        result = plugin.execute_tool(
            name="bash_run",
            arguments={"command": "export TEST_VALUE=value && echo nope"},
        )

        assert result.lower().startswith("error:")
        assert "TEST_VALUE" not in shell_session.get_session_context()["env_vars"]

    @pytest.mark.asyncio
    async def test_subagent_runs_child_pipeline_with_forked_tape(self, tmp_path):
        parent_tape = Tape(
            entries=[
                Entry(
                    kind="message", payload={"role": "user", "content": "parent task"}
                ),
                Entry(
                    kind="tool_call",
                    payload={
                        "id": "tool-1",
                        "name": "bash_run",
                        "arguments": {"command": "pwd"},
                    },
                ),
            ]
        )
        parent_provider = object()
        parent_doom_state = {"doom_detected": True}
        parent_ctx = PipelineContext(
            tape=parent_tape,
            session_id="parent",
            config={"subagent_timeout": 30.0},
            llm_provider=parent_provider,
            plugin_states={"doom_detector": parent_doom_state},
        )
        captured: dict[str, object] = {}

        class FakeChildPipeline:
            _registry = object()

            async def mount(self, ctx: PipelineContext) -> None:
                del ctx

            async def run_turn(self, ctx: PipelineContext) -> None:
                ctx.tape.append(
                    Entry(
                        kind="message",
                        payload={"role": "assistant", "content": "Child solved it"},
                    )
                )

        def build_child_pipeline(
            *,
            parent_provider: object,
            tape_fork: Tape,
            tool_filter,
            session_id_override: str | None = None,
            agent_id_override: str | None = None,
            parent_run_id_override: str | None = None,
            context_budget: object | None = None,
            trace_metadata: dict[str, object] | None = None,
        ):
            child_registry = ToolRegistry()

            @tool(name="file_read", description="Read a file")
            def file_read(path: str) -> str:
                return path

            @tool(name="subagent", description="Nested subagent")
            def nested_subagent(goal: str) -> str:
                return goal

            child_registry.register(file_read)
            child_registry.register(nested_subagent)
            allowed_names = [
                name for name in child_registry.names() if tool_filter(name)
            ]
            child_registry.retain(allowed_names)

            child_ctx = PipelineContext(
                tape=tape_fork,
                session_id="child",
                config={"tool_registry": child_registry},
                llm_provider=parent_provider,
            )
            captured["parent_provider"] = parent_provider
            captured["tape_parent_id"] = tape_fork.parent_id
            captured["fork_entry_count_before_turn"] = len(tape_fork)
            captured["child_registry_names"] = child_registry.names()
            captured["child_plugin_states"] = child_ctx.plugin_states
            captured["session_id_override"] = session_id_override
            captured["agent_id_override"] = agent_id_override
            captured["parent_run_id_override"] = parent_run_id_override
            captured["trace_metadata"] = trace_metadata
            return FakeChildPipeline(), child_ctx

        executor = CoreToolExecutor(
            workspace_root=tmp_path,
            child_pipeline_builder=build_child_pipeline,
        )

        result = await executor.execute_tool_async(
            name="subagent",
            arguments={"goal": "Investigate child task"},
            ctx=parent_ctx,
        )

        assert result == "Subagent completed: Child solved it"
        assert captured["parent_provider"] is parent_provider
        assert captured["tape_parent_id"] == parent_tape.tape_id
        assert captured["fork_entry_count_before_turn"] == 1
        assert captured["child_registry_names"] == ["file_read"]
        assert captured["child_plugin_states"] == {}
        assert captured["child_plugin_states"] is not parent_ctx.plugin_states
        assert captured["session_id_override"] == parent_ctx.session_id
        assert captured["agent_id_override"] == "child-1"
        assert captured["parent_run_id_override"] is None
        assert captured["trace_metadata"] == {
            "subagent.parent_agent_id": "",
            "subagent.child_agent_id": "child-1",
        }
        assert parent_ctx.plugin_states["doom_detector"] is parent_doom_state
        appended_entries = list(parent_tape)[2:]
        assert len(appended_entries) == 2
        assert appended_entries[0].kind == "message"
        assert appended_entries[0].payload["content"] == "Investigate child task"
        assert appended_entries[1].kind == "message"
        assert appended_entries[1].payload["content"] == "Child solved it"
        assert all(entry.meta.get("skip_context") is True for entry in appended_entries)
