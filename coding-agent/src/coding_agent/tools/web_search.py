from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from agentkit.tools import tool


class WebSearchBackend(Protocol):
    def search(self, query: str, limit: int) -> list[dict[str, str]]: ...


@dataclass
class MockWebSearchBackend:
    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        return [
            {
                "title": f"Mock result for {query}",
                "url": "https://example.com/mock-result",
                "snippet": f"Mock snippet for {query}",
            }
        ][:limit]


def build_web_search_tool(backend: WebSearchBackend | None):
    resolved_backend = backend if backend is not None else MockWebSearchBackend()

    @tool(
        name="web_search",
        description="Search the web and return structured result summaries.",
    )
    def web_search(query: str, limit: int = 5) -> str:
        normalized_query = query.strip()
        if not normalized_query:
            return json.dumps({"error": "query must not be empty"})

        results = resolved_backend.search(normalized_query, limit)
        return json.dumps({"query": normalized_query, "results": results})

    return web_search


def create_web_search_backend(config: dict[str, Any] | None = None) -> WebSearchBackend:
    backend_name = (config or {}).get("backend", "mock")
    if backend_name == "mock":
        return MockWebSearchBackend()
    raise ValueError(f"unsupported web_search backend: {backend_name}")
