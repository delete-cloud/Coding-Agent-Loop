"""Codex ChatGPT-authenticated Responses provider."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agentkit.providers.models import (
    DoneEvent,
    StreamEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    UsageEvent,
)
from coding_agent.oauth.types import OAuthTokenSource, TokenSnapshot

logger = logging.getLogger(__name__)

DEFAULT_ORIGINATOR = "codex_cli_rs"
ORIGINATOR_OVERRIDE_ENV = "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"
DEFAULT_USER_AGENT = "codex_cli_rs/0.135.0"


class CodexResponsesProvider:
    """Codex provider using the official ChatGPT-backed Responses wire API."""

    CONTEXT_SIZES: dict[str, int] = {
        "gpt-5.5": 400000,
        "gpt-5.4": 400000,
        "gpt-5.2": 400000,
    }

    def __init__(
        self,
        *,
        model: str,
        token_source: OAuthTokenSource,
        base_url: str,
        timeout: float = 60.0,
        originator: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._token_source = token_source
        self._base_url = base_url.rstrip("/")
        self._originator = (
            os.environ.get(ORIGINATOR_OVERRIDE_ENV) or originator or DEFAULT_ORIGINATOR
        )
        self._user_agent = user_agent
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_size(self) -> int:
        return self.CONTEXT_SIZES.get(self._model, 128000)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        request = self._build_request(messages, tools=tools, extra=kwargs)
        snapshot = await self._token_source.get_token()

        async for event in self._stream_with_snapshot(request, snapshot):
            yield event

    async def _stream_with_snapshot(
        self,
        request: dict[str, Any],
        snapshot: TokenSnapshot,
    ) -> AsyncIterator[StreamEvent]:
        refreshed_after_unauthorized = False
        current_snapshot = snapshot

        while True:
            async with self._http_client.stream(
                "POST",
                f"{self._base_url}/responses",
                headers=self._headers(current_snapshot),
                json=request,
            ) as response:
                if response.status_code == 401 and not refreshed_after_unauthorized:
                    await response.aread()
                    refreshed_after_unauthorized = True
                    current_snapshot = await self._token_source.refresh_token()
                    continue

                if response.status_code >= 400:
                    text = await response.aread()
                    raise RuntimeError(
                        f"Codex Responses request failed with status "
                        f"{response.status_code}: "
                        f"{text.decode('utf-8', errors='replace')}"
                    )

                async for event in self._events_from_response(response):
                    yield event
                return

    def _headers(self, snapshot: TokenSnapshot) -> dict[str, str]:
        account_id = snapshot.account.chatgpt_account_id
        if not account_id:
            raise RuntimeError(
                "Codex OAuth record is missing ChatGPT account id. "
                "Run `coding-agent oauth login codex` again."
            )

        thread_id = str(uuid.uuid4())
        return {
            "Authorization": f"Bearer {snapshot.access_token}",
            "ChatGPT-Account-ID": account_id,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "originator": self._originator,
            "User-Agent": self._user_agent,
            "session-id": str(uuid.uuid4()),
            "thread-id": thread_id,
            "x-client-request-id": thread_id,
            "x-codex-installation-id": "coding-agent",
        }

    async def _events_from_response(
        self,
        response: httpx.Response,
    ) -> AsyncIterator[StreamEvent]:
        pending_event: str | None = None
        data_lines: list[str] = []

        async for raw_line in response.aiter_lines():
            line = raw_line.rstrip("\r")
            if line == "":
                async for event in self._events_from_sse_payload(
                    pending_event,
                    "\n".join(data_lines),
                ):
                    yield event
                pending_event = None
                data_lines = []
                continue
            if line.startswith("event:"):
                pending_event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())

        if data_lines:
            async for event in self._events_from_sse_payload(
                pending_event,
                "\n".join(data_lines),
            ):
                yield event

    async def _events_from_sse_payload(
        self,
        event_name: str | None,
        data: str,
    ) -> AsyncIterator[StreamEvent]:
        if not data or data == "[DONE]":
            return

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("Ignoring malformed Codex SSE payload for %s", event_name)
            return

        kind = _str(payload.get("type")) or event_name
        if kind == "response.output_text.delta":
            delta = _str(payload.get("delta"))
            if delta:
                yield TextEvent(text=delta)
            return

        if kind in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
            delta = _str(payload.get("delta"))
            if delta:
                yield ThinkingEvent(text=delta)
            return

        if kind == "response.output_item.done":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                yield _tool_call_event_from_item(item)
            return

        if kind == "response.failed":
            raise RuntimeError(_response_error_message(payload, "Codex response failed"))

        if kind == "response.incomplete":
            raise RuntimeError(_response_error_message(payload, "Codex response incomplete"))

        if kind == "response.completed":
            response = payload.get("response")
            usage = response.get("usage") if isinstance(response, dict) else None
            if isinstance(usage, dict):
                yield UsageEvent(
                    input_tokens=_int(usage.get("input_tokens")),
                    output_tokens=_int(usage.get("output_tokens")),
                    provider_name=self._model,
                )
            yield DoneEvent()

    def _build_request(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[Any] | None,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        instructions, input_items = _convert_messages(messages)
        if not instructions:
            raise ValueError("Codex Responses requests require a system instruction message")

        body: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": input_items,
            "stream": True,
            "store": False,
            "parallel_tool_calls": True,
            "client_metadata": {"x-codex-installation-id": "coding-agent"},
        }
        converted_tools = _convert_tools(tools)
        if converted_tools:
            body["tools"] = converted_tools
        body.update(extra)
        return body

    async def close(self) -> None:
        await self._http_client.aclose()

    async def __aenter__(self) -> CodexResponsesProvider:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.close()


def _convert_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in messages:
        role = _str(message.get("role"))
        if role == "system":
            text = _message_text(message.get("content"))
            if text:
                instructions.append(text)
            continue

        if role in ("user", "assistant"):
            text = _message_text(message.get("content"))
            if text:
                input_items.append(_message_item(role, text))
            for tool_call in _message_tool_calls(message):
                input_items.append(tool_call)
            continue

        if role == "tool":
            call_id = _str(message.get("tool_call_id"))
            if not call_id:
                raise ValueError("tool messages require tool_call_id for Codex Responses")
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _message_text(message.get("content")),
                }
            )

    return "\n\n".join(instructions), input_items


def _message_item(role: str, text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": text}],
    }


def _message_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    calls: list[dict[str, Any]] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            continue
        call_id = _str(raw_call.get("id"))
        name = _str(function.get("name"))
        arguments = function.get("arguments")
        if not call_id or not name:
            raise ValueError("assistant tool_calls require id and function.name")
        calls.append(
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
            }
        )
    return calls


def _convert_tools(tools: list[Any] | None) -> list[dict[str, Any]]:
    if not tools:
        return []

    converted: list[dict[str, Any]] = []
    for tool in tools:
        tool_dict = tool.to_openai_format() if hasattr(tool, "to_openai_format") else tool
        if not isinstance(tool_dict, dict) or tool_dict.get("type") != "function":
            continue
        function = tool_dict.get("function")
        if not isinstance(function, dict):
            continue
        name = _str(function.get("name"))
        if not name:
            raise ValueError("function tools require a name")
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": _str(function.get("description")) or "",
                "parameters": function.get("parameters") or {"type": "object"},
            }
        )
    return converted


def _tool_call_event_from_item(item: dict[str, Any]) -> ToolCallEvent:
    arguments_raw = item.get("arguments")
    arguments: dict[str, Any]
    if isinstance(arguments_raw, str) and arguments_raw:
        try:
            parsed = json.loads(arguments_raw)
        except json.JSONDecodeError:
            parsed = {}
        arguments = parsed if isinstance(parsed, dict) else {}
    elif isinstance(arguments_raw, dict):
        arguments = arguments_raw
    else:
        arguments = {}

    return ToolCallEvent(
        tool_call_id=_str(item.get("call_id")) or _str(item.get("id")),
        name=_str(item.get("name")),
        arguments=arguments,
    )


def _message_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)
    return json.dumps(content)


def _response_error_message(payload: dict[str, Any], fallback: str) -> str:
    response = payload.get("response")
    if not isinstance(response, dict):
        return fallback
    error = response.get("error")
    if not isinstance(error, dict):
        return fallback
    message = error.get("message")
    return message if isinstance(message, str) and message else fallback


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
