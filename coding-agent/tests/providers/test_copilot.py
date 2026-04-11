from coding_agent.providers.copilot import CopilotProvider


class TestCopilotProvider:
    def test_defaults_to_github_models_base_url_and_headers(self):
        provider = CopilotProvider(model="gpt-4.1", api_key="ghu-test")

        assert str(provider._client.base_url) == "https://models.github.ai/inference/"
        assert (
            provider._client.default_headers["Accept"] == "application/vnd.github+json"
        )
