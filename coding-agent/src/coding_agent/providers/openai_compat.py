"""OpenAI-compatible provider implementation."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
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

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream LLM response.
        
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
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=openai_tools,
                stream=True,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
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
                if chunk.choices[0].finish_reason == "tool_calls":
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

            yield StreamEvent(type="done")

        except Exception as e:
            yield StreamEvent(type="error", error=str(e))
