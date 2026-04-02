import pytest

from agentkit.providers.protocol import LLMProvider
from coding_agent.plugins.llm_provider import LLMProviderPlugin
from coding_agent.providers.anthropic import AnthropicProvider
from coding_agent.providers.copilot import CopilotProvider
from coding_agent.providers.openai_compat import OpenAICompatProvider


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


class TestKimiProvider:
    def test_kimi_creates_openai_compat_instance(self):
        plugin = LLMProviderPlugin(
            provider="kimi",
            model="moonshot-v1-128k",
            api_key="sk-test-kimi",
        )
        result = plugin.provide_llm()
        assert isinstance(result, OpenAICompatProvider)
        assert result.model_name == "moonshot-v1-128k"

    def test_kimi_uses_moonshot_base_url(self):
        plugin = LLMProviderPlugin(
            provider="kimi",
            model="moonshot-v1-32k",
            api_key="sk-test",
        )
        result = plugin.provide_llm()
        assert "moonshot.cn" in str(result._client.base_url)

    def test_kimi_context_sizes(self):
        for model, expected in [
            ("moonshot-v1-8k", 8192),
            ("moonshot-v1-32k", 32768),
            ("moonshot-v1-128k", 131072),
            ("kimi-k2-0711-preview", 131072),
            ("moonshot-v1-auto", 131072),
        ]:
            p = OpenAICompatProvider(model=model, api_key="x")
            assert p.max_context_size == expected

    def test_moonshot_api_key_env_fallback(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-from-env")
        plugin = LLMProviderPlugin(
            provider="kimi",
            model="moonshot-v1-8k",
            api_key="",
        )
        result = plugin.provide_llm()
        assert "sk-from-env" in str(result._client.api_key)

    def test_kimi_explicit_key_takes_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-from-env")
        plugin = LLMProviderPlugin(
            provider="kimi",
            model="moonshot-v1-8k",
            api_key="sk-explicit",
        )
        result = plugin.provide_llm()
        assert "sk-explicit" in str(result._client.api_key)


class TestKimiCodeOpenAIProvider:
    def test_kimi_code_creates_openai_compat_instance(self):
        plugin = LLMProviderPlugin(
            provider="kimi-code",
            model="kimi-for-coding",
            api_key="sk-kimi-test",
        )
        result = plugin.provide_llm()
        assert isinstance(result, OpenAICompatProvider)
        assert result.model_name == "kimi-for-coding"

    def test_kimi_code_uses_kimi_com_base_url(self):
        plugin = LLMProviderPlugin(
            provider="kimi-code",
            model="kimi-for-coding",
            api_key="sk-kimi-test",
        )
        result = plugin.provide_llm()
        assert "api.kimi.com/coding/v1" in str(result._client.base_url)

    def test_kimi_code_sets_claude_code_user_agent(self):
        plugin = LLMProviderPlugin(
            provider="kimi-code",
            model="kimi-for-coding",
            api_key="sk-kimi-test",
        )
        result = plugin.provide_llm()
        headers = result._client.default_headers
        assert headers.get("User-Agent") == "claude-code/1.0.17"

    def test_kimi_for_coding_context_size(self):
        p = OpenAICompatProvider(model="kimi-for-coding", api_key="x")
        assert p.max_context_size == 262144

    def test_kimi_code_api_key_env_fallback(self, monkeypatch):
        monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-from-env")
        plugin = LLMProviderPlugin(
            provider="kimi-code",
            model="kimi-for-coding",
            api_key="",
        )
        result = plugin.provide_llm()
        assert "sk-kimi-from-env" in str(result._client.api_key)

    def test_kimi_code_explicit_key_takes_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-from-env")
        plugin = LLMProviderPlugin(
            provider="kimi-code",
            model="kimi-for-coding",
            api_key="sk-explicit",
        )
        result = plugin.provide_llm()
        assert "sk-explicit" in str(result._client.api_key)


class TestKimiCodeAnthropicProvider:
    def test_kimi_code_anthropic_creates_anthropic_instance(self):
        plugin = LLMProviderPlugin(
            provider="kimi-code-anthropic",
            model="kimi-for-coding",
            api_key="sk-kimi-test",
        )
        result = plugin.provide_llm()
        assert isinstance(result, AnthropicProvider)
        assert result.model_name == "kimi-for-coding"

    def test_kimi_code_anthropic_uses_kimi_com_base_url(self):
        plugin = LLMProviderPlugin(
            provider="kimi-code-anthropic",
            model="kimi-for-coding",
            api_key="sk-kimi-test",
        )
        result = plugin.provide_llm()
        assert "api.kimi.com/coding" in str(result._base_url)

    def test_kimi_code_anthropic_api_key_env_fallback(self, monkeypatch):
        monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-from-env")
        plugin = LLMProviderPlugin(
            provider="kimi-code-anthropic",
            model="kimi-for-coding",
            api_key="",
        )
        result = plugin.provide_llm()
        assert result._api_key == "sk-kimi-from-env"

    def test_kimi_code_anthropic_explicit_key_takes_priority_over_env(
        self, monkeypatch
    ):
        monkeypatch.setenv("KIMI_CODE_API_KEY", "sk-kimi-from-env")
        plugin = LLMProviderPlugin(
            provider="kimi-code-anthropic",
            model="kimi-for-coding",
            api_key="sk-explicit",
        )
        result = plugin.provide_llm()
        assert result._api_key == "sk-explicit"

    def test_kimi_code_anthropic_sets_claude_code_user_agent(self):
        plugin = LLMProviderPlugin(
            provider="kimi-code-anthropic",
            model="kimi-for-coding",
            api_key="sk-kimi-test",
        )
        result = plugin.provide_llm()
        headers = result._client.default_headers
        assert headers.get("User-Agent") == "claude-code/1.0.17"
