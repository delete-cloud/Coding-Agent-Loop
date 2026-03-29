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
