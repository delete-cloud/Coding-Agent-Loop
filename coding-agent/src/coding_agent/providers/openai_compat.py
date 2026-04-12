"""OpenAI-compatible provider implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator

import httpx
from openai import AsyncOpenAI, APIError, RateLimitError, APIStatusError

from agentkit.providers.models import (
    StreamEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    DoneEvent,
    UsageEvent,
)
from coding_agent.utils.retry import _extract_status_code, RETRYABLE_STATUS_CODES

logger = logging.getLogger(__name__)


class OpenAICompatProvider:
    """OpenAI-compatible provider (works with OpenAI, Deepseek, Qwen, etc.)."""

    CONTEXT_SIZES: dict[str, int] = {
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        "deepseek-chat": 65536,
        "deepseek-coder": 65536,
        "moonshot-v1-8k": 8192,
        "moonshot-v1-32k": 32768,
        "moonshot-v1-128k": 131072,
        "kimi-k2-0711-preview": 131072,
        "moonshot-v1-auto": 131072,
        "kimi-for-coding": 262144,
    }

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        max_connections: int = 10,
        max_keepalive: int = 5,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0,
        default_headers: dict[str, str] | None = None,
    ):
        self._model = model
        if hasattr(api_key, "get_secret_value"):
            api_key = api_key.get_secret_value()

        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        )
        timeout_config = httpx.Timeout(timeout)

        self._http_client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout_config,
        )

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            http_client=self._http_client,
        )
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_size(self) -> int:
        return self.CONTEXT_SIZES.get(self._model, 128000)

    def _convert_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            if hasattr(tool, "to_openai_format"):
                result.append(tool.to_openai_format())
            elif hasattr(tool, "function"):
                result.append({"type": "function", "function": tool.function})
            else:
                result.append(tool)
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        openai_tools = None
        if tools:
            openai_tools = self._convert_tools(tools)

        last_exception: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                stream = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=openai_tools,
                    stream=True,
                    stream_options={"include_usage": True},
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    **kwargs,
                )

                accumulating_calls: dict[int, dict[str, Any]] = {}
                _usage_in = 0
                _usage_out = 0

                async for chunk in stream:
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage:
                        _usage_in = getattr(chunk_usage, "prompt_tokens", 0) or 0
                        _usage_out = getattr(chunk_usage, "completion_tokens", 0) or 0

                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]

                    choice_usage = getattr(choice, "usage", None)
                    if choice_usage:
                        _usage_in = getattr(choice_usage, "prompt_tokens", 0) or 0
                        _usage_out = getattr(choice_usage, "completion_tokens", 0) or 0

                    delta = choice.delta

                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield ThinkingEvent(text=reasoning)

                    if delta.content:
                        yield TextEvent(text=delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in accumulating_calls:
                                accumulating_calls[idx] = {
                                    "id": tc.id or "",
                                    "name": "",
                                    "arguments": "",
                                }

                            if tc.function:
                                if tc.function.name:
                                    accumulating_calls[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    accumulating_calls[idx]["arguments"] += (
                                        tc.function.arguments
                                    )

                    finish_reason = choice.finish_reason
                    if finish_reason in ("tool_calls", "stop") and accumulating_calls:
                        for idx in sorted(accumulating_calls.keys()):
                            call_data = accumulating_calls[idx]
                            try:
                                args = (
                                    json.loads(call_data["arguments"])
                                    if call_data["arguments"]
                                    else {}
                                )
                            except json.JSONDecodeError:
                                args = {}

                            yield ToolCallEvent(
                                tool_call_id=call_data["id"],
                                name=call_data["name"],
                                arguments=args,
                            )
                        accumulating_calls.clear()

                yield UsageEvent(
                    input_tokens=_usage_in,
                    output_tokens=_usage_out,
                    provider_name=self._model,
                )
                yield DoneEvent()
                return

            except Exception as e:
                last_exception = e

                if attempt >= self._max_retries:
                    logger.debug(f"Max retries ({self._max_retries}) exceeded")
                    break

                status_code = _extract_status_code(e)

                if status_code is None:
                    logger.debug(
                        f"No status code found in {type(e).__name__}, raising immediately"
                    )
                    break

                if status_code not in RETRYABLE_STATUS_CODES:
                    logger.debug(
                        f"Status {status_code} not retryable, raising immediately"
                    )
                    break

                delay = min(
                    self._retry_base_delay * (2**attempt), self._retry_max_delay
                )
                delay += random.uniform(0, 1)

                logger.warning(
                    f"stream failed (attempt {attempt + 1}/{self._max_retries + 1}): "
                    f"{type(e).__name__}{f' (status={status_code})' if status_code else ''}, "
                    f"retrying in {delay:.2f}s..."
                )

                await asyncio.sleep(delay)

        if last_exception:
            raise last_exception

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
