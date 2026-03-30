import pytest
from pathlib import Path

from coding_agent.__main__ import create_agent


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
        assert approval_plugin._policy.name == "MANUAL"
