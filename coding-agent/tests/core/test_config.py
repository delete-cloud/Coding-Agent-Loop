import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from coding_agent.core.config import Config, load_config


class TestConfig:
    def test_defaults(self):
        c = Config(api_key="sk-test")
        assert c.model == "gpt-4o"
        assert c.provider == "openai"
        assert c.max_steps == 30
        assert c.doom_threshold == 3
        assert c.approval_mode == "yolo"

    def test_api_key_optional(self):
        # api_key is now optional (None) to support testing and HTTP server modes
        c = Config()
        assert c.api_key is None

    def test_api_key_is_secret(self):
        c = Config(api_key="sk-secret")
        assert "sk-secret" not in repr(c)
        assert c.api_key.get_secret_value() == "sk-secret"

    def test_custom_values(self):
        c = Config(
            api_key="sk-test",
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            base_url="https://api.example.com/v1",
            max_steps=10,
            doom_threshold=5,
            repo=Path("/tmp/test-repo"),
        )
        assert c.model == "claude-sonnet-4-20250514"
        assert c.provider == "anthropic"
        assert c.base_url == "https://api.example.com/v1"
        assert c.max_steps == 10
        assert c.repo == Path("/tmp/test-repo")


class TestLoadConfig:
    def test_env_vars_override_defaults(self, monkeypatch):
        monkeypatch.setenv("AGENT_API_KEY", "sk-from-env")
        monkeypatch.setenv("AGENT_MODEL", "gpt-4o-mini")
        c = load_config()
        assert c.api_key.get_secret_value() == "sk-from-env"
        assert c.model == "gpt-4o-mini"

    def test_cli_args_override_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_API_KEY", "sk-from-env")
        monkeypatch.setenv("AGENT_MODEL", "gpt-4o-mini")
        c = load_config(cli_args={"model": "gpt-4o", "api_key": "sk-cli"})
        assert c.model == "gpt-4o"
        assert c.api_key.get_secret_value() == "sk-cli"

    def test_missing_api_key_allows_none(self, monkeypatch):
        # api_key is optional - missing env var results in None, not error
        monkeypatch.delenv("AGENT_API_KEY", raising=False)
        c = load_config()
        assert c.api_key is None
