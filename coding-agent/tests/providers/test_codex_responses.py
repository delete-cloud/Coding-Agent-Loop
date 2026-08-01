from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent, UsageEvent
from coding_agent.oauth.types import OAuthAccount, TokenSnapshot
from coding_agent.providers.codex_responses import CodexResponsesProvider


class MemoryTokenSource:
    def __init__(self, access_token: str = "access-token") -> None:
        self.access_token = access_token
        self.refresh_count = 0

    async def get_token(self) -> TokenSnapshot:
        return TokenSnapshot(
            provider_name="codex",
            access_token=self.access_token,
            account=OAuthAccount(chatgpt_account_id="account-123"),
        )

    async def refresh_token(self) -> TokenSnapshot:
        self.refresh_count += 1
        self.access_token = "new-token"
        return await self.get_token()


def sse_event(kind: str, payload: dict[str, object]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload)}\n\n"


def provider_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token_source: MemoryTokenSource | None = None,
) -> CodexResponsesProvider:
    return CodexResponsesProvider(
        model="gpt-5.5",
        token_source=token_source or MemoryTokenSource(),
        base_url="https://chatgpt.com/backend-api/codex",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_stream_posts_to_codex_responses_with_chatgpt_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", raising=False)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        body = "".join(
            [
                sse_event(
                    "response.output_text.delta",
                    {"type": "response.output_text.delta", "delta": "hel"},
                ),
                sse_event(
                    "response.output_text.delta",
                    {"type": "response.output_text.delta", "delta": "lo"},
                ),
                sse_event(
                    "response.completed",
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_1",
                            "usage": {
                                "input_tokens": 7,
                                "output_tokens": 2,
                                "total_tokens": 9,
                            },
                        },
                    },
                ),
            ]
        )
        return httpx.Response(200, text=body, request=request)

    provider = provider_with_handler(handler)

    events = [
        event
        async for event in provider.stream(
            [
                {"role": "system", "content": "You are concise."},
                {"role": "user", "content": "Say hello"},
            ]
        )
    ]

    assert captured["url"] == "https://chatgpt.com/backend-api/codex/responses"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer access-token"
    assert headers["chatgpt-account-id"] == "account-123"
    assert headers["accept"] == "text/event-stream"
    assert headers["originator"] == "codex_cli_rs"

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-5.5"
    assert body["instructions"] == "You are concise."
    assert body["stream"] is True
    assert body["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Say hello"}],
        }
    ]

    assert events == [
        TextEvent(text="hel"),
        TextEvent(text="lo"),
        UsageEvent(input_tokens=7, output_tokens=2, provider_name="gpt-5.5"),
        DoneEvent(),
    ]


@pytest.mark.asyncio
async def test_stream_translates_thinking_config_to_reasoning_effort() -> None:
    """thinking_config must not reach the backend verbatim (400
    'Unsupported parameter'); it maps to Responses reasoning.effort."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        body = sse_event(
            "response.completed",
            {"type": "response.completed", "response": {"id": "r"}},
        )
        return httpx.Response(200, text=body, request=request)

    provider = provider_with_handler(handler)

    async for _ in provider.stream(
        [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Hi"},
        ],
        thinking_config={"enabled": True, "effort": "high"},
    ):
        pass

    body = captured["body"]
    assert "thinking_config" not in body
    assert body["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_stream_omits_reasoning_when_thinking_disabled() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        body = sse_event(
            "response.completed",
            {"type": "response.completed", "response": {"id": "r"}},
        )
        return httpx.Response(200, text=body, request=request)

    provider = provider_with_handler(handler)

    async for _ in provider.stream(
        [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Hi"},
        ],
        thinking_config={"enabled": False, "effort": "low"},
    ):
        pass

    body = captured["body"]
    assert "thinking_config" not in body
    assert "reasoning" not in body


@pytest.mark.asyncio
async def test_stream_converts_function_call_output_item_done() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = sse_event(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "shell",
                    "arguments": '{"command": "pwd"}',
                },
            },
        ) + sse_event(
            "response.completed",
            {"type": "response.completed", "response": {"id": "resp_1"}},
        )
        return httpx.Response(200, text=body, request=request)

    provider = provider_with_handler(handler)

    events = [
        event
        async for event in provider.stream(
            [
                {"role": "system", "content": "You are concise."},
                {"role": "user", "content": "Run pwd"},
            ]
        )
    ]

    assert events == [
        ToolCallEvent(
            tool_call_id="call_123",
            name="shell",
            arguments={"command": "pwd"},
        ),
        DoneEvent(),
    ]


@pytest.mark.asyncio
async def test_stream_converts_assistant_history_as_output_text() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        body = sse_event(
            "response.completed",
            {"type": "response.completed", "response": {"id": "resp_1"}},
        )
        return httpx.Response(200, text=body, request=request)

    provider = provider_with_handler(handler)

    events = [
        event
        async for event in provider.stream(
            [
                {"role": "system", "content": "You are concise."},
                {"role": "user", "content": "你是谁"},
                {"role": "assistant", "content": "我是一个 AI 编程助手。"},
                {"role": "user", "content": "你能看到上个请求吗"},
            ]
        )
    ]

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "你是谁"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "我是一个 AI 编程助手。"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "你能看到上个请求吗"}],
        },
    ]
    assert events == [DoneEvent()]


@pytest.mark.asyncio
async def test_stream_refreshes_token_once_on_unauthorized() -> None:
    seen_auth: list[str] = []
    token_source = MemoryTokenSource("old-token")

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["authorization"])
        if len(seen_auth) == 1:
            return httpx.Response(401, json={"detail": "expired"}, request=request)
        body = sse_event(
            "response.completed",
            {"type": "response.completed", "response": {"id": "resp_1"}},
        )
        return httpx.Response(200, text=body, request=request)

    provider = provider_with_handler(handler, token_source=token_source)

    events = [
        event
        async for event in provider.stream(
            [
                {"role": "system", "content": "You are concise."},
                {"role": "user", "content": "Say ok"},
            ]
        )
    ]

    assert seen_auth == ["Bearer old-token", "Bearer new-token"]
    assert token_source.refresh_count == 1
    assert events == [DoneEvent()]


@pytest.mark.asyncio
async def test_list_models_filters_hidden_and_sorts_by_priority() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-5.5", "visibility": "list", "priority": 4},
                    {"slug": "gpt-5.6-sol", "visibility": "list", "priority": 1},
                    {"slug": "gpt-internal", "visibility": "hide", "priority": 0},
                    {"slug": "gpt-5.4", "visibility": "list", "priority": 5},
                    {"slug": "gpt-5.6-luna", "visibility": "list", "priority": 3},
                    {"slug": "gpt-5.6-terra", "visibility": "list", "priority": 2},
                    # Defensive: malformed entries are skipped.
                    {"visibility": "list", "priority": 6},
                    {"slug": "", "visibility": "list", "priority": 7},
                    {"slug": "gpt-no-priority", "visibility": "list"},
                    "not-a-dict",
                ],
            },
            request=request,
        )

    provider = provider_with_handler(handler)

    models = await provider.list_models()

    assert models == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
    ]
    assert captured["url"] == (
        "https://chatgpt.com/backend-api/codex/models?client_version=0.0.0"
    )
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer access-token"
    assert headers["chatgpt-account-id"] == "account-123"
    assert headers["originator"] == "codex_cli_rs"


@pytest.mark.asyncio
async def test_list_models_refreshes_token_once_on_unauthorized() -> None:
    seen_auth: list[str] = []
    token_source = MemoryTokenSource("old-token")

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["authorization"])
        if len(seen_auth) == 1:
            return httpx.Response(401, json={"detail": "expired"}, request=request)
        return httpx.Response(
            200,
            json={"models": [{"slug": "gpt-5.5", "visibility": "list", "priority": 1}]},
            request=request,
        )

    provider = provider_with_handler(handler, token_source=token_source)

    models = await provider.list_models()

    assert seen_auth == ["Bearer old-token", "Bearer new-token"]
    assert token_source.refresh_count == 1
    assert models == ["gpt-5.5"]


@pytest.mark.asyncio
async def test_list_models_raises_with_status_and_body_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="backend exploded", request=request)

    provider = provider_with_handler(handler)

    with pytest.raises(RuntimeError, match="500.*backend exploded"):
        await provider.list_models()


@pytest.mark.asyncio
async def test_list_models_returns_empty_for_empty_or_malformed_listing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Missing account id on the backend yields 200 with an empty list.
        return httpx.Response(200, json={"models": []}, request=request)

    provider = provider_with_handler(handler)

    assert await provider.list_models() == []

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"}, request=request)

    provider = provider_with_handler(malformed_handler)

    assert await provider.list_models() == []
