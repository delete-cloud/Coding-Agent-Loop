from __future__ import annotations

from coding_agent.providers.openai_compat import OpenAICompatProvider


class CopilotProvider(OpenAICompatProvider):
    DEFAULT_BASE_URL = "https://models.github.ai/inference"
    DEFAULT_HEADERS = {"Accept": "application/vnd.github+json"}

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            default_headers=self.DEFAULT_HEADERS,
            **kwargs,
        )
