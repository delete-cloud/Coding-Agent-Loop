from __future__ import annotations

import json

import pytest

from coding_agent.tools.web_search import build_web_search_tool


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        self.calls.append((query, limit))
        return [
            {
                "title": "Result 1",
                "url": "https://example.com/1",
                "snippet": f"snippet for {query}",
            }
        ]


def test_web_search_tool_returns_structured_json() -> None:
    backend = RecordingBackend()
    tool_fn = build_web_search_tool(backend)

    result = tool_fn(query="agent architecture", limit=3)
    payload = json.loads(result)

    assert payload["query"] == "agent architecture"
    assert payload["results"][0]["title"] == "Result 1"
    assert backend.calls == [("agent architecture", 3)]


def test_web_search_tool_rejects_empty_query() -> None:
    backend = RecordingBackend()
    tool_fn = build_web_search_tool(backend)

    result = tool_fn(query="   ")
    payload = json.loads(result)

    assert payload["error"] == "query must not be empty"


def test_web_search_tool_uses_mock_backend_when_backend_missing() -> None:
    tool_fn = build_web_search_tool(None)

    result = tool_fn(query="agent architecture")
    payload = json.loads(result)

    assert payload["results"][0]["title"] == "Mock result for agent architecture"
