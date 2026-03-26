"""Anthropic native provider (Claude models)."""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic, APIError, RateLimitError, APIStatusError

from coding_agent.providers.base import (
    StreamEvent,
    ToolCall,
    ToolSchema,
)


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
    ):
        # Handle both plain strings and Pydantic SecretStr
        api_key_str = api_key
        if not isinstance(api_key, str):
            # Assume it's a SecretStr or similar with get_secret_value
            api_key_str = api_key.get_secret_value()
        self._model = model
        self._client = AsyncAnthropic(api_key=api_key_str)
        self._max_tokens = max_tokens
        self._temperature = temperature

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_size(self) -> int:
        return self.DEFAULT_CONTEXT_SIZE

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 1.0
    RETRY_STATUS_CODES = {429, 500, 502, 503, 529}

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
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except json.JSONDecodeError as e:
                            logging.warning(f"Failed to parse tool arguments JSON: {e}, args_str={args_str!r}")
                            args = {"_parse_error": str(e), "_raw": args_str}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": func["name"],
                            "input": args,
                        })
                    anthropic_msgs.append({"role": "assistant", "content": content_blocks})
                else:
                    anthropic_msgs.append({"role": "assistant", "content": msg.get("content", "")})

            elif role == "tool":
                # Tool results → user message with tool_result content block
                anthropic_msgs.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg["tool_call_id"],
                            "content": msg.get("content", ""),
                        }
                    ],
                })

        return "\n\n".join(system_parts), anthropic_msgs

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Convert ToolSchema list to Anthropic tool format."""
        result = []
        for tool in tools:
            func = tool.function
            result.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream response from Anthropic API.

        Translates Anthropic streaming events to internal StreamEvent format.
        """
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

        try:
            # Track tool use blocks being accumulated
            tool_blocks: dict[int, dict[str, Any]] = {}

            for attempt in range(self.MAX_RETRIES):
                try:
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
                                        yield StreamEvent(type="delta", text=delta.text)
                                    elif delta.type == "input_json_delta":
                                        idx = event.index
                                        if idx in tool_blocks:
                                            tool_blocks[idx]["input_json"] += delta.partial_json
                                case "content_block_stop":
                                    idx = event.index
                                    if idx in tool_blocks:
                                        block = tool_blocks.pop(idx)
                                        try:
                                            args = json.loads(block["input_json"]) if block["input_json"] else {}
                                        except json.JSONDecodeError:
                                            args = {}
                                        yield StreamEvent(
                                            type="tool_call",
                                            tool_call=ToolCall(
                                                id=block["id"],
                                                name=block["name"],
                                                arguments=args,
                                            ),
                                        )
                                case "message_stop":
                                    pass  # handled below

                    yield StreamEvent(type="done")
                    return  # success, exit retry loop

                except RateLimitError:
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self.RETRY_DELAY_BASE * (2 ** attempt) + random.uniform(0, 1)
                        await asyncio.sleep(delay)
                        continue
                    raise
                except APIStatusError as e:
                    if e.status_code in self.RETRY_STATUS_CODES and attempt < self.MAX_RETRIES - 1:
                        delay = self.RETRY_DELAY_BASE * (2 ** attempt) + random.uniform(0, 1)
                        await asyncio.sleep(delay)
                        continue
                    raise

        except RateLimitError as e:
            yield StreamEvent(type="error", error=f"Rate limit exceeded: {e}")
        except APIStatusError as e:
            yield StreamEvent(type="error", error=f"API error {e.status_code}: {e}")
        except APIError as e:
            yield StreamEvent(type="error", error=f"API error: {e}")
        except Exception as e:
            # Catch specific non-critical exceptions only
            # Let BaseException subclasses (KeyboardInterrupt, SystemExit) propagate
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            yield StreamEvent(type="error", error=f"Unexpected error: {e}")
