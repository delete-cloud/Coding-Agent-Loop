import pytest
from pathlib import Path

from agentkit.runtime import AgentRunContext, ContextBudget
from coding_agent.__main__ import create_agent
from coding_agent.environment import LocalEnvironment


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
        plugin_ids = pipeline._registry.plugin_ids()
        assert "llm_provider" in plugin_ids
        assert "storage" in plugin_ids
        assert "core_tools" in plugin_ids
        assert "approval" in plugin_ids
        assert "memory" in plugin_ids
        assert "doom_detector" in plugin_ids
        assert "parallel_executor" in plugin_ids
        assert "session_metrics" in plugin_ids
        assert "kb" not in plugin_ids

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
