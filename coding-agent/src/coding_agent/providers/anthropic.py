"""Anthropic native provider (Claude models)."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic, APIError, RateLimitError, APIStatusError

from agentkit.providers.models import (
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    DoneEvent,
)
from coding_agent.utils.retry import _extract_status_code, RETRYABLE_STATUS_CODES

logger = logging.getLogger(__name__)


class AnthropicProvider:
    """Anthropic provider using native API (not OpenAI-compatible).

    Translates between internal OpenAI-format messages and Anthropic's
    content block API format.
    """

    # All Claude models share 200k context
    DEFAULT_CONTEXT_SIZE = 200000

    def __init__(
        self,
        model: str,
        api_key: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0,
    ):
        # Handle both plain strings and Pydantic SecretStr
        api_key_str = api_key
        if not isinstance(api_key, str):
            # Assume it's a SecretStr or similar with get_secret_value
            api_key_str = api_key.get_secret_value()
        self._model = model
        self._api_key = api_key_str
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self.__client: AsyncAnthropic | None = None

    @property
    def _client(self) -> AsyncAnthropic:
        if self.__client is None:
            self.__client = AsyncAnthropic(
                api_key=self._api_key,
                timeout=self._timeout,
            )
        return self.__client

    @_client.setter
    def _client(self, value: AsyncAnthropic | None) -> None:
        self.__client = value

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_size(self) -> int:
        return self.DEFAULT_CONTEXT_SIZE

    def _convert_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert OpenAI-format messages to Anthropic format.

        Returns:
            (system_prompt, anthropic_messages)
        """
        system_parts: list[str] = []
        anthropic_msgs: list[dict[str, Any]] = []

        for msg in messages:
            role = msg["role"]

            if role == "system":
                system_parts.append(msg["content"])

            elif role == "user":
                anthropic_msgs.append({"role": "user", "content": msg["content"]})

            elif role == "assistant":
                # May have tool_calls → convert to content blocks
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    content_blocks = []
                    # Add text if present
                    if msg.get("content"):
                        content_blocks.append({"type": "text", "text": msg["content"]})
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        args_str = func.get("arguments", "{}")
                        try:
                            args = (
                                json.loads(args_str)
                                if isinstance(args_str, str)
                                else args_str
                            )
                        except json.JSONDecodeError as e:
                            logging.warning(
                                f"Failed to parse tool arguments JSON: {e}, args_str={args_str!r}"
                            )
                            args = {"_parse_error": str(e), "_raw": args_str}
                        content_blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": func["name"],
                                "input": args,
                            }
                        )
                    anthropic_msgs.append(
                        {"role": "assistant", "content": content_blocks}
                    )
                else:
                    anthropic_msgs.append(
                        {"role": "assistant", "content": msg.get("content", "")}
                    )

            elif role == "tool":
                # Tool results → user message with tool_result content block
                anthropic_msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg["tool_call_id"],
                                "content": msg.get("content", ""),
                            }
                        ],
                    }
                )

        return "\n\n".join(system_parts), anthropic_msgs

    def _convert_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            # agentkit ToolSchema — has to_openai_format()
            if hasattr(tool, "to_openai_format"):
                func = tool.to_openai_format()["function"]
            # old base.ToolSchema — has .function dict
            elif hasattr(tool, "function"):
                func = tool.function
            else:
                # Handle OpenAI-format dicts: {"type": "function", "function": {...}}
                if isinstance(tool, dict) and "function" in tool:
                    func = tool["function"]
                else:
                    func = tool
            result.append(
                {
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        system_prompt, anthropic_msgs = self._convert_messages(messages)

        api_kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": anthropic_msgs,
            "temperature": self._temperature,
        }
        if system_prompt:
            api_kwargs["system"] = system_prompt
        if tools:
            api_kwargs["tools"] = self._convert_tools(tools)

        last_exception: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                # Track tool use blocks being accumulated
                tool_blocks: dict[int, dict[str, Any]] = {}

                async with self._client.messages.stream(**api_kwargs) as stream:
                    async for event in stream:
                        match event.type:
                            case "content_block_start":
                                block = event.content_block
                                if block.type == "tool_use":
                                    tool_blocks[event.index] = {
                                        "id": block.id,
                                        "name": block.name,
                                        "input_json": "",
                                    }
                            case "content_block_delta":
                                delta = event.delta
                                if delta.type == "text_delta":
                                    yield TextEvent(text=delta.text)
                                elif delta.type == "input_json_delta":
                                    idx = event.index
                                    if idx in tool_blocks:
                                        tool_blocks[idx]["input_json"] += (
                                            delta.partial_json
                                        )
                            case "content_block_stop":
                                idx = event.index
                                if idx in tool_blocks:
                                    block = tool_blocks.pop(idx)
                                    try:
                                        args = (
                                            json.loads(block["input_json"])
                                            if block["input_json"]
                                            else {}
                                        )
                                    except json.JSONDecodeError:
                                        args = {}
                                    yield ToolCallEvent(
                                        tool_call_id=block["id"],
                                        name=block["name"],
                                        arguments=args,
                                    )
                            case "message_stop":
                                pass

                yield DoneEvent()
                return  # Success, exit the retry loop

            except Exception as e:
                last_exception = e

                # Check if this is the last attempt
                if attempt >= self._max_retries:
                    logger.debug(f"Max retries ({self._max_retries}) exceeded")
                    break

                # Extract status code from exception
                status_code = _extract_status_code(e)

                # Only retry if we have a status code and it's in retryable list
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

                # Calculate delay with exponential backoff and jitter
                delay = min(
                    self._retry_base_delay * (2**attempt), self._retry_max_delay
                )
                delay += random.uniform(0, 1)  # Add jitter

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
        if self.__client:
            await self.__client.close()
            self.__client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
