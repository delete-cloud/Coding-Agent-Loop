import pytest

from agentkit.providers.protocol import LLMProvider
from coding_agent.plugins.llm_provider import LLMProviderPlugin
from coding_agent.providers.copilot import CopilotProvider


class TestLLMProviderPlugin:
    def test_state_key(self):
        plugin = LLMProviderPlugin(
            provider="anthropic", model="claude-sonnet", api_key="sk-test"
        )
        assert plugin.state_key == "llm_provider"

    def test_hooks_include_provide_llm(self):
        plugin = LLMProviderPlugin(
            provider="anthropic", model="claude-sonnet", api_key="sk-test"
        )
        hooks = plugin.hooks()
        assert "provide_llm" in hooks

    def test_provide_llm_returns_provider_instance(self):
        plugin = LLMProviderPlugin(
            provider="anthropic", model="claude-sonnet", api_key="sk-test"
        )
        result = plugin.provide_llm()
        assert isinstance(result, LLMProvider)

    def test_provide_llm_openai(self):
        plugin = LLMProviderPlugin(provider="openai", model="gpt-4", api_key="sk-test")
        result = plugin.provide_llm()
        assert isinstance(result, LLMProvider)
        assert result.model_name == "gpt-4"

    def test_provide_llm_copilot(self):
        plugin = LLMProviderPlugin(
            provider="copilot", model="gpt-4.1", api_key="ghu-test"
        )

        result = plugin.provide_llm()

        assert isinstance(result, LLMProvider)
        assert isinstance(result, CopilotProvider)
        assert result.model_name == "gpt-4.1"

    def test_unknown_provider_raises(self):
        plugin = LLMProviderPlugin(provider="unknown", model="x", api_key="sk-test")
        with pytest.raises(ValueError, match="unsupported provider"):
            plugin.provide_llm()
