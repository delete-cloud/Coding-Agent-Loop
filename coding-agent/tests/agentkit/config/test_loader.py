import pytest
from pathlib import Path
from agentkit.config.loader import load_config, AgentConfig
from agentkit.errors import ConfigError


class TestAgentConfig:
    def test_config_fields(self):
        cfg = AgentConfig(
            name="my-agent",
            model="gpt-4",
            provider="openai",
            system_prompt="You are helpful.",
            plugins=["core_tools", "memory"],
            max_turns=30,
        )
        assert cfg.name == "my-agent"
        assert cfg.model == "gpt-4"
        assert cfg.plugins == ["core_tools", "memory"]

    def test_config_defaults(self):
        cfg = AgentConfig(name="test", model="gpt-4", provider="openai")
        assert cfg.system_prompt == ""
        assert cfg.plugins == []
        assert cfg.max_turns == 30


class TestLoadConfig:
    def test_load_valid_toml(self, tmp_path):
        toml_file = tmp_path / "agent.toml"
        toml_file.write_text("""
[agent]
name = "test-agent"
model = "claude-sonnet"
provider = "anthropic"
system_prompt = "You are a coding assistant."
max_turns = 50

[agent.plugins]
enabled = ["core_tools", "memory", "shell_session"]

[storage]
tape_backend = "jsonl"
doc_backend = "lancedb"

[storage.paths]
tapes = "./data/tapes"
docs = "./data/docs"
sessions = "./data/sessions"
""")
        cfg = load_config(toml_file)
        assert cfg.name == "test-agent"
        assert cfg.model == "claude-sonnet"
        assert cfg.provider == "anthropic"
        assert "core_tools" in cfg.plugins
        assert cfg.max_turns == 50
        assert cfg.extra["storage"]["tape_backend"] == "jsonl"

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.toml")

    def test_load_invalid_toml_raises(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("this is not valid toml [[[")
        with pytest.raises(ConfigError, match="parse"):
            load_config(bad)

    def test_load_missing_agent_section_raises(self, tmp_path):
        toml_file = tmp_path / "agent.toml"
        toml_file.write_text('[storage]\nbackend = "jsonl"\n')
        with pytest.raises(ConfigError, match="\\[agent\\] section"):
            load_config(toml_file)

    def test_load_missing_required_field_raises(self, tmp_path):
        toml_file = tmp_path / "agent.toml"
        toml_file.write_text('[agent]\nname = "test"\n')
        with pytest.raises(ConfigError, match="model"):
            load_config(toml_file)

    def test_load_toml_exposes_subagent_timeout_section(self, tmp_path):
        toml_file = tmp_path / "agent.toml"
        toml_file.write_text(
            """
[agent]
name = "test-agent"
model = "claude-sonnet"
provider = "anthropic"

[subagent]
timeout = 7.5
""".strip()
        )

        cfg = load_config(toml_file)

        assert cfg.extra["subagent"]["timeout"] == 7.5
