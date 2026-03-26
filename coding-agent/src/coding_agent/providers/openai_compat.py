"""OpenAI-compatible provider implementation."""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError, RateLimitError, APIStatusError
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall

from coding_agent.providers.base import (
    ChatProvider,
    StreamEvent,
    ToolCall,
    ToolSchema,
)


class OpenAICompatProvider:
    """OpenAI-compatible provider (works with OpenAI, Deepseek, Qwen, etc.)."""

    # Model context sizes (in tokens)
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
    ):
        self._model = model
        # Handle SecretStr by extracting actual value
        if hasattr(api_key, 'get_secret_value'):
            api_key = api_key.get_secret_value()
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._max_tokens = max_tokens
        self._temperature = temperature

    @property
    def model_name(self) -> str:
        """Name of the model being used."""
        return self._model

    @property
    def max_context_size(self) -> int:
        """Maximum context size in tokens."""
        return self.CONTEXT_SIZES.get(self._model, 128000)

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 1.0  # seconds
    RETRY_STATUS_CODES = {429, 500, 502, 503, 529}  # Rate limit + server errors

    async def _make_request_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ):
        """Make API request with exponential backoff retry."""
        for attempt in range(self.MAX_RETRIES):
            try:
                return await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                    stream=True,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    **kwargs,
                )
            except RateLimitError as e:
                # Always retry rate limits with longer delay
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAY_BASE * (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
                    continue
                raise
            except APIStatusError as e:
                # Retry on specific status codes
                if e.status_code in self.RETRY_STATUS_CODES and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAY_BASE * (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(delay)
                    continue
                raise
            except Exception:
                # Don't retry other errors (auth, validation, etc.)
                raise

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream LLM response with retry logic.
        
        Args:
            messages: List of message dicts
            tools: Optional list of tool schemas
            **kwargs: Additional options
            
        Yields:
            StreamEvent objects
        """
        # Convert tools to OpenAI format
        openai_tools = None
        if tools:
            openai_tools = [
                {"type": "function", "function": tool.function}
                for tool in tools
            ]

        try:
            stream = await self._make_request_with_retry(
                messages=messages,
                tools=openai_tools,
                **kwargs,
            )

            # Track accumulating tool calls
            accumulating_calls: dict[int, dict[str, Any]] = {}

            async for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Handle text content
                if delta.content:
                    yield StreamEvent(type="delta", text=delta.content)

                # Handle tool calls
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
                                accumulating_calls[idx]["arguments"] += tc.function.arguments

                # Check for finished tool calls (when finish_reason is present)
                # Some APIs return "stop" even for tool calls, so check if we have accumulated calls
                finish_reason = chunk.choices[0].finish_reason
                if finish_reason in ("tool_calls", "stop") and accumulating_calls:
                    for idx in sorted(accumulating_calls.keys()):
                        call_data = accumulating_calls[idx]
                        try:
                            args = json.loads(call_data["arguments"]) if call_data["arguments"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        
                        yield StreamEvent(
                            type="tool_call",
                            tool_call=ToolCall(
                                id=call_data["id"],
                                name=call_data["name"],
                                arguments=args,
                            ),
                        )
                    accumulating_calls.clear()  # Clear after yielding

            yield StreamEvent(type="done")

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
