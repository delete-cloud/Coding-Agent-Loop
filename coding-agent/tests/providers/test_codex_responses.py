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
