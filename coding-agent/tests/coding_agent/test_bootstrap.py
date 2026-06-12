from pathlib import Path
from typing import Any, cast

import pytest
from agentkit.environment import WorkspaceSummary
from agentkit.observability import NoopObservationSink
from agentkit.runtime import AgentRunContext, ContextBudget
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tools import tool
from agentkit.tape.tape import Tape
from coding_agent.__main__ import create_agent, create_child_pipeline
from coding_agent.environment import (
    CloudCommandResult,
    CloudEnvironment,
    LocalEnvironment,
)
from coding_agent.observability import (
    CompositeObservationSink,
    PrometheusMetricsObservationSink,
)


class NonLocalEnvironment:
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
            return f"read:{path}"

        @tool(name="file_write", description="Write a file")
        def file_write(path: str, content: str) -> str:
            return f"write:{path}:{content}"

        @tool(name="file_replace", description="Replace text in a file")
        def file_replace(path: str, old: str, new: str) -> str:
            return f"replace:{path}:{old}:{new}"

        @tool(name="glob_files", description="Glob files")
        def glob_files(pattern: str, directory: str = ".") -> str:
            return f"glob:{directory}:{pattern}"

        @tool(name="grep_search", description="Search files")
        def grep_search(pattern: str, directory: str = ".", include: str = "") -> str:
            return f"grep:{directory}:{pattern}:{include}"

        return file_read, file_write, file_replace, glob_files, grep_search

    def build_file_patch_tool(self):
        @tool(name="file_patch", description="Apply a patch")
        def file_patch(path: str, patch: str) -> str:
            return f"patch:{path}:{patch}"

        return file_patch

    def build_shell_tool(self):
        @tool(name="bash_run", description="Run a shell command")
        def bash_run(
            command: str,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
        ) -> str:
            return f"shell:{cwd}:{command}:{env or {}}"

        return bash_run


class CloudTraceClient:
    workspace_id: str = "ws-trace-123"
    workspace_url: str = "https://workspace.example.com?token=secret"
    default_cwd: str = "/workspace"

    def read_file(self, path: str) -> str:
        return path

    def write_file(self, path: str, content: str) -> None:
        del path, content

    def replace_file(self, path: str, old: str, new: str) -> None:
        del path, old, new

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return []

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del pattern, directory, include
        return []

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        del patch
        return {"success": True, "path": path, "changed": False}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        del command, cwd, env, timeout
        return CloudCommandResult(stdout="", stderr="", exit_code=0)


class TestBootstrap:
    def test_create_agent_returns_pipeline_and_context(self, tmp_path):
        from agentkit.runtime.pipeline import Pipeline, PipelineContext

        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )
        assert isinstance(pipeline, Pipeline)
        assert isinstance(ctx, PipelineContext)

    def test_all_plugins_registered(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path,
            api_key="sk-test",
        )
        plugin_ids = pipeline._registry.plugin_ids()
        assert "llm_provider" in plugin_ids
        assert "storage" in plugin_ids
        assert "core_tools" in plugin_ids
        assert "approval" in plugin_ids
        assert "memory" in plugin_ids
        assert "doom_detector" in plugin_ids
        assert "parallel_executor" in plugin_ids
        assert "topic" not in plugin_ids
        assert "session_metrics" in plugin_ids
        assert "kb" not in plugin_ids

    def test_default_plugin_set_excludes_legacy_topic_plugin(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"
""".strip()
        )

        pipeline, _ = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert "topic" not in pipeline._registry.plugin_ids()

    def test_explicit_legacy_topic_plugin_config_fails_closed(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["topic"]
""".strip()
        )

        with pytest.raises(ValueError, match="unsupported plugin in config: topic"):
            create_agent(
                config_path=config_path,
                data_dir=tmp_path / "data",
                api_key="sk-test",
            )

    def test_kb_plugin_requires_explicit_enablement(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools", "kb"]

[kb]
db_path = "kb"
embedding_model = "text-embedding-3-small"
embedding_base_url = "https://embed.example/v1"
embedding_dim = 1536
chunk_size = 1200
chunk_overlap = 200
top_k = 5
index_extensions = [".md"]
""".strip()
        )

        pipeline, _ = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert "kb" in pipeline._registry.plugin_ids()
        kb_plugin = pipeline._registry.get("kb")
        assert kb_plugin._embedding_base_url == "https://embed.example/v1"

    def test_create_agent_does_not_inject_observation_sink_by_default(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]
""".strip()
        )

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert "observation_sink" not in ctx.config

    def test_create_agent_injects_noop_observation_sink_when_enabled(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]

[observability]
enabled = true
backend = "noop"
""".strip()
        )

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert isinstance(ctx.config["observation_sink"], NoopObservationSink)

    def test_create_agent_injects_composite_observation_sink_for_tracing_and_metrics(
        self,
        tmp_path,
    ):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]

[observability]
enabled = true

[observability.tracing]
enabled = true
backend = "noop"

[observability.metrics]
enabled = true
backend = "prometheus"
""".strip()
        )

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        sink = ctx.config["observation_sink"]
        assert isinstance(sink, CompositeObservationSink)
        assert isinstance(sink.sinks[0], NoopObservationSink)
        assert isinstance(sink.sinks[1], PrometheusMetricsObservationSink)

    def test_create_agent_rejects_unknown_observability_backend(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]

[observability]
enabled = true
backend = "bogus"
""".strip()
        )

        with pytest.raises(ValueError, match="unsupported observability backend"):
            create_agent(
                config_path=config_path,
                data_dir=tmp_path / "data",
                api_key="sk-test",
            )

    def test_create_agent_respects_enabled_plugins_order(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]
""".strip()
        )

        pipeline, _ = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert pipeline._registry.plugin_ids() == ["storage", "core_tools"]

    def test_create_agent_uses_mcp_servers_override(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["mcp"]

[mcp.servers.from_toml]
command = "toml-server"
args = ["--toml"]
""".strip()
        )

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            mcp_servers_override={
                "from_acp": {
                    "command": "acp-server",
                    "args": ["--stdio"],
                    "env": {"TOKEN": "redacted"},
                }
            },
        )

        mcp_plugin = pipeline._registry.get("mcp")
        assert ctx.config["mcp_plugin"] is mcp_plugin
        assert [cfg.name for cfg in mcp_plugin._server_configs] == ["from_acp"]
        assert mcp_plugin._server_configs[0].command == "acp-server"
        assert mcp_plugin._server_configs[0].args == ["--stdio"]
        assert mcp_plugin._server_configs[0].env == {"TOKEN": "redacted"}

    def test_create_agent_uses_additional_workspace_roots_override(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()
        (extra / "note.txt").write_text("from extra")
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]
""".strip()
        )

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            workspace_root=workspace,
            additional_workspace_roots_override=[str(extra)],
        )

        assert ctx.config["additional_workspace_roots"] == [str(extra.resolve())]
        core_tools = pipeline._registry.get("core_tools")
        result = core_tools.execute_tool(
            name="file_read",
            arguments={"path": str(extra / "note.txt")},
            ctx=ctx,
        )
        assert result == "from extra"

    def test_create_agent_applies_runtime_overrides(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            model_override="gpt-test",
            provider_override="openai",
            base_url_override="http://localhost:1234/v1",
            workspace_root=workspace_root,
            max_steps_override=7,
            approval_mode_override="interactive",
        )

        assert ctx.config["model"] == "gpt-test"
        assert ctx.config["provider"] == "openai"
        assert ctx.config["max_tool_rounds"] == 7

        llm_plugin = pipeline._registry.get("llm_provider")
        assert llm_plugin._provider_name == "openai"
        assert llm_plugin._model == "gpt-test"
        assert llm_plugin._base_url == "http://localhost:1234/v1"

        core_tools = pipeline._registry.get("core_tools")
        assert core_tools._workspace_root == workspace_root.resolve()

        approval_plugin = pipeline._registry.get("approval")
        assert approval_plugin._policy.name == "INTERACTIVE"

    def test_create_agent_uses_stepfun_api_key_env_fallback(
        self, tmp_path, monkeypatch
    ):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")
        monkeypatch.delenv("AGENT_API_KEY", raising=False)
        monkeypatch.setenv("STEP_API_KEY", "sk-stepfun-from-env")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key=None,
            provider_override="stepfun",
            model_override="step-3.7-flash",
        )

        assert ctx.config["provider"] == "stepfun"
        assert ctx.config["model"] == "step-3.7-flash"
        llm_plugin = pipeline._registry.get("llm_provider")
        assert llm_plugin._provider_name == "stepfun"
        assert llm_plugin._api_key == "sk-stepfun-from-env"

    def test_create_agent_uses_injected_environment(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        workspace_root = tmp_path / "env-workspace"
        environment = LocalEnvironment(workspace_root=workspace_root)

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            workspace_root=tmp_path / "ignored-workspace",
            environment=environment,
        )

        assert ctx.config["environment"] is environment
        assert ctx.config["workspace_root"] == str(workspace_root.resolve())
        core_tools = pipeline._registry.get("core_tools")
        assert core_tools._environment is environment
        assert core_tools._workspace_root == workspace_root.resolve()

    def test_create_agent_accepts_non_local_environment_without_workspace_root(
        self, tmp_path
    ):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools", "shell_session"]
""".strip()
        )
        environment = NonLocalEnvironment()

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            environment=environment,
        )

        assert ctx.config["environment"] is environment
        assert "workspace_root" not in ctx.config
        assert ctx.run_context.environment is environment
        core_tools = pipeline._registry.get("core_tools")
        assert core_tools._environment is environment
        assert core_tools._workspace_root is None

    def test_create_agent_builds_agent_run_context(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        workspace_root = tmp_path / "env-workspace"
        environment = LocalEnvironment(workspace_root=workspace_root)

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            model_override="gpt-test",
            provider_override="openai",
            base_url_override="http://localhost:1234/v1",
            max_steps_override=7,
            session_id_override="session-1",
            environment=environment,
        )

        assert isinstance(ctx.run_context, AgentRunContext)
        assert ctx.run_context.session_id == "session-1"
        assert ctx.run_context.run_id
        assert ctx.run_context.agent_id is None
        assert ctx.run_context.parent_run_id is None
        assert ctx.run_context.environment is environment
        assert isinstance(ctx.run_context.context_budget, ContextBudget)
        assert ctx.run_context.trace_metadata == {}
        # Existing UI/wire code still uses "" as the root-agent marker.
        assert ctx.config["agent_id"] == ""
        assert ctx.config["provider"] == "openai"
        assert ctx.config["model"] == "gpt-test"
        assert ctx.config["max_tool_rounds"] == 7

    def test_create_agent_adds_secret_free_cloud_workspace_trace_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        _pipeline, raw_ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            model_override="gpt-test",
            provider_override="openai",
            base_url_override="http://localhost:1234/v1",
            session_id_override="session-1",
            environment=CloudEnvironment(CloudTraceClient()),
            trace_metadata={
                "request_id": "req-1",
                "cloud.workspace_id": "stale-spoofed-id",
            },
        )
        ctx = cast(PipelineContext, raw_ctx)

        assert ctx.run_context is not None
        assert ctx.run_context.trace_metadata == {
            "request_id": "req-1",
            "cloud.workspace_id": "ws-trace-123",
        }
        assert "workspace_url" not in ctx.run_context.trace_metadata
        assert "secret" not in str(dict(ctx.run_context.trace_metadata))

    def test_create_agent_strips_cloud_trace_metadata_for_local_environment(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        _pipeline, raw_ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
            model_override="gpt-test",
            provider_override="openai",
            base_url_override="http://localhost:1234/v1",
            session_id_override="session-1",
            environment=LocalEnvironment(tmp_path / "workspace"),
            trace_metadata={
                "request_id": "req-1",
                "cloud.workspace_id": "stale-cloud-id",
                "cloud.workspace_url": "https://workspace.example.com?token=secret",
            },
        )
        ctx = cast(PipelineContext, raw_ctx)

        assert ctx.run_context is not None
        assert ctx.run_context.trace_metadata == {"request_id": "req-1"}
        assert "cloud." not in str(dict(ctx.run_context.trace_metadata))
        assert "secret" not in str(dict(ctx.run_context.trace_metadata))

    def test_create_agent_normalizes_explicit_none_agent_id_override(self, tmp_path):
        """Explicit None must produce run_context.agent_id is None and "" in legacy config."""
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        environment = LocalEnvironment(workspace_root=tmp_path / "env-workspace")

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="[REDACTED:api-key]",
            model_override="gpt-test",
            provider_override="openai",
            base_url_override="http://localhost:1234/v1",
            session_id_override="session-1",
            agent_id_override=None,
            environment=environment,
        )

        assert ctx.run_context.agent_id is None
        assert ctx.config["agent_id"] == ""

    def test_create_agent_rejects_empty_session_id_override(self, tmp_path):
        """Empty (but not None) session_id_override must fail fast, not become a uuid."""
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        environment = LocalEnvironment(workspace_root=tmp_path / "env-workspace")

        with pytest.raises(ValueError, match="session_id_override"):
            create_agent(
                config_path=config_path,
                data_dir=tmp_path / "data",
                api_key="[REDACTED:api-key]",
                model_override="gpt-test",
                provider_override="openai",
                base_url_override="http://localhost:1234/v1",
                session_id_override="",
                environment=environment,
            )

    def test_create_agent_applies_run_id_override(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        environment = LocalEnvironment(workspace_root=tmp_path / "env-workspace")

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="[REDACTED:api-key]",
            model_override="gpt-test",
            provider_override="openai",
            base_url_override="http://localhost:1234/v1",
            session_id_override="session-1",
            run_id_override="run-1",
            environment=environment,
        )

        assert ctx.run_context.run_id == "run-1"

    def test_create_child_pipeline_applies_run_id_override(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        environment = LocalEnvironment(workspace_root=tmp_path / "env-workspace")

        _pipeline, ctx = create_child_pipeline(
            parent_provider=None,
            tape_fork=Tape(),
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="[REDACTED:api-key]",
            model_override="gpt-test",
            provider_override="openai",
            base_url_override="http://localhost:1234/v1",
            session_id_override="session-1",
            run_id_override="run-1",
            environment=environment,
        )

        assert ctx.run_context.run_id == "run-1"

    def test_create_agent_rejects_empty_run_id_override(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        environment = LocalEnvironment(workspace_root=tmp_path / "env-workspace")

        with pytest.raises(ValueError, match="run_id_override"):
            create_agent(
                config_path=config_path,
                data_dir=tmp_path / "data",
                api_key="[REDACTED:api-key]",
                model_override="gpt-test",
                provider_override="openai",
                base_url_override="http://localhost:1234/v1",
                session_id_override="session-1",
                run_id_override="",
                environment=environment,
            )

    def test_create_child_pipeline_rejects_empty_run_id_override(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        environment = LocalEnvironment(workspace_root=tmp_path / "env-workspace")

        with pytest.raises(ValueError, match="run_id_override"):
            create_child_pipeline(
                parent_provider=None,
                tape_fork=Tape(),
                config_path=config_path,
                data_dir=tmp_path / "data",
                api_key="[REDACTED:api-key]",
                model_override="gpt-test",
                provider_override="openai",
                base_url_override="http://localhost:1234/v1",
                session_id_override="session-1",
                run_id_override="",
                environment=environment,
            )

    def test_create_agent_reads_subagent_timeout_from_config(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]

[subagent]
timeout = 7.5
""".strip()
        )

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert ctx.config["subagent_timeout"] == 7.5

    def test_create_agent_uses_subagent_timeout_fallback_when_config_missing(
        self, tmp_path
    ):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet-4-20250514"
provider = "anthropic"

[agent.plugins]
enabled = ["storage", "core_tools"]
""".strip()
        )

        _pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert ctx.config["subagent_timeout"] == 30.0
