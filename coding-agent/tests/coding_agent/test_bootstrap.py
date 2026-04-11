import pytest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

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
        assert "kb" in plugin_ids
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
        assert approval_plugin._policy.name == "INTERACTIVE"

    def test_create_agent_exposes_tool_registry_in_context(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        core_tools = pipeline._registry.get("core_tools")

        assert ctx.config["tool_registry"] is core_tools.registry

    def test_create_agent_loads_subagent_timeout_from_config(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        _, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        assert "subagent_timeout" in ctx.config
        assert isinstance(ctx.config["subagent_timeout"], float)

    def test_create_child_pipeline_reuses_provider_and_tape_fork(self, tmp_path):
        from agentkit.tape.tape import Tape

        create_child_pipeline = import_module("coding_agent.app").create_child_pipeline

        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        shared_provider = object()
        tape_fork = Tape()

        pipeline, ctx = create_child_pipeline(
            parent_provider=shared_provider,
            tape_fork=tape_fork,
            tool_filter=lambda name: name != "web_search",
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        llm_plugin = pipeline._registry.get("llm_provider")
        core_tools = pipeline._registry.get("core_tools")

        assert ctx.tape is tape_fork
        assert llm_plugin.provide_llm() is shared_provider
        assert "web_search" not in core_tools.registry.names()
        assert "web_search" not in {schema.name for schema in core_tools.get_tools()}

    def test_create_agent_builds_default_pipeline_via_child_factory(self, tmp_path):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        sentinel = object()

        with patch(
            "coding_agent.app.create_child_pipeline", return_value=sentinel
        ) as mock_child:
            result = create_agent(
                config_path=config_path,
                data_dir=tmp_path / "data",
                api_key="sk-test",
            )

        assert result is sentinel
        call_kwargs = mock_child.call_args.kwargs
        assert call_kwargs["parent_provider"] is None
        assert call_kwargs["tool_filter"] is None
        assert call_kwargs["api_key"] == "sk-test"
        assert call_kwargs["data_dir"] == tmp_path / "data"
        assert call_kwargs["tape_fork"].__class__.__name__ == "Tape"

    def test_create_agent_loads_web_search_config(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "gpt-4.1"
provider = "copilot"

[agent.plugins]
enabled = ["core_tools", "approval"]

[approval]
policy = "auto"

[web_search]
backend = "mock"
""".strip()
        )

        pipeline, ctx = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        core_tools = pipeline._registry.get("core_tools")
        approval_plugin = pipeline._registry.get("approval")

        assert core_tools._web_search_backend is not None
        assert ctx.config["web_search"] == {"backend": "mock"}
        assert "web_search" in {schema.name for schema in core_tools.get_tools()}
        assert "web_search" in approval_plugin._external_request_tools

    def test_create_agent_uses_agent_data_dir_env_when_data_dir_not_provided(
        self, tmp_path, monkeypatch
    ):
        config_path = (
            Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
        )
        if not config_path.exists():
            pytest.skip("agent.toml not found")

        env_data_dir = tmp_path / "env-data"
        monkeypatch.setenv("AGENT_DATA_DIR", str(env_data_dir))

        pipeline, _ = create_agent(
            config_path=config_path,
            api_key="sk-test",
        )

        storage = pipeline._registry.get("storage")
        assert storage._data_dir == env_data_dir

    def test_create_agent_registers_kb_plugin_when_enabled(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "gpt-4.1"
provider = "copilot"

[agent.plugins]
enabled = ["llm_provider", "storage", "kb"]

[storage]
session_backend = "file"

[kb]
db_path = "kb"
embedding_model = "text-embedding-3-small"
embedding_dim = 8
chunk_size = 50
chunk_overlap = 10
top_k = 3
index_extensions = [".md"]
""".strip()
        )

        pipeline, _ = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "data",
            api_key="sk-test",
        )

        plugin_ids = pipeline._registry.plugin_ids()

        assert "kb" in plugin_ids

    def test_create_agent_wires_kb_plugin_config(self, tmp_path):
        config_path = tmp_path / "agent.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "gpt-4.1"
provider = "copilot"

[agent.plugins]
enabled = ["llm_provider", "storage", "kb"]

[storage]
session_backend = "file"

[kb]
db_path = "kb-data"
embedding_model = "text-embedding-3-small"
embedding_dim = 8
chunk_size = 75
chunk_overlap = 15
top_k = 4
index_extensions = [".md", ".txt"]
""".strip()
        )

        pipeline, _ = create_agent(
            config_path=config_path,
            data_dir=tmp_path / "agent-data",
            api_key="sk-test",
        )

        kb_plugin = pipeline._registry.get("kb")

        assert kb_plugin._db_path == (tmp_path / "agent-data" / "kb-data")
        assert kb_plugin._embedding_dim == 8
        assert kb_plugin._chunk_size == 75
        assert kb_plugin._chunk_overlap == 15
        assert kb_plugin._top_k == 4
        assert kb_plugin._index_extensions == [".md", ".txt"]
