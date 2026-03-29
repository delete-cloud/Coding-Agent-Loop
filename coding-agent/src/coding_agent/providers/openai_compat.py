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
    ToolCallEvent,
    DoneEvent,
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
    ):
        self._model = model
        # Handle SecretStr by extracting actual value
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
            # agentkit ToolSchema — has to_openai_format()
            if hasattr(tool, "to_openai_format"):
                result.append(tool.to_openai_format())
            # old base.ToolSchema — has .function dict
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
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    **kwargs,
                )

                accumulating_calls: dict[int, dict[str, Any]] = {}

                async for chunk in stream:
                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta

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

                    finish_reason = chunk.choices[0].finish_reason
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

                yield DoneEvent()
                return  # Success, exit the retry loop

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

        # All retries exhausted or non-retryable error — raise instead of yielding error events
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
