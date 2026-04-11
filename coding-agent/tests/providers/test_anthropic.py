"""Tests for Anthropic provider."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coding_agent.providers.anthropic import AnthropicProvider
from coding_agent.providers.base import ToolCall, ToolSchema
from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent, UsageEvent


class TestAnthropicProviderInit:
    def test_init_basic(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        assert p.model_name == "claude-sonnet-4-20250514"
        assert p.max_context_size == 200000

    def test_init_custom_model(self):
        p = AnthropicProvider(model="claude-haiku-4-5-20251001", api_key="sk-test")
        assert p.model_name == "claude-haiku-4-5-20251001"
        assert p.max_context_size == 200000

    def test_init_unknown_model_default_context(self):
        p = AnthropicProvider(model="future-model", api_key="sk-test")
        assert p.max_context_size == 200000


class TestMessageConversion:
    """Test conversion from OpenAI-format messages to Anthropic format."""

    def test_convert_user_message(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, converted = p._convert_messages(messages)
        assert system == "You are helpful."
        assert len(converted) == 1
        assert converted[0] == {"role": "user", "content": "Hello"}

    def test_convert_multiple_system_messages_concatenated(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        messages = [
            {"role": "system", "content": "Rule 1."},
            {"role": "system", "content": "Rule 2."},
            {"role": "user", "content": "Hi"},
        ]
        system, converted = p._convert_messages(messages)
        assert system == "Rule 1.\n\nRule 2."
        assert len(converted) == 1

    def test_convert_assistant_with_tool_calls(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Read foo.py"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "foo.py"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "file contents here",
            },
        ]
        system, converted = p._convert_messages(messages)
        assert system == "sys"
        assert len(converted) == 3  # user, assistant, user(tool_result)

        # Assistant message should have tool_use content block
        assistant_msg = converted[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"][0]["type"] == "tool_use"
        assert assistant_msg["content"][0]["id"] == "call_1"
        assert assistant_msg["content"][0]["name"] == "file_read"

        # Tool result should be user message with tool_result content block
        tool_msg = converted[2]
        assert tool_msg["role"] == "user"
        assert tool_msg["content"][0]["type"] == "tool_result"
        assert tool_msg["content"][0]["tool_use_id"] == "call_1"

    def test_convert_tool_schemas(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        schemas = [
            ToolSchema(
                type="function",
                function={
                    "name": "bash",
                    "description": "Run a command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            )
        ]
        result = p._convert_tools(schemas)
        assert len(result) == 1
        assert result[0]["name"] == "bash"
        assert result[0]["description"] == "Run a command"
        assert result[0]["input_schema"]["type"] == "object"


class TestAnthropicStreaming:
    @pytest.mark.asyncio
    async def test_stream_text_response(self):
        """Test text-only response streaming."""
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        # Mock the Anthropic streaming events
        mock_events = [
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=MagicMock(type="text", text=""),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text="Hello"),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text=" world"),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_stop"),
        ]

        # Create an async iterator for the mock stream - __aiter__ receives self
        def mock_aiter(_self):
            async def _aiter():
                for e in mock_events:
                    yield e

            return _aiter()

        # Create a mock stream context manager
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.__aiter__ = mock_aiter

        # Mock the stream method to return the context manager
        p._client.messages.stream = MagicMock(return_value=mock_stream)

        events = []
        async for event in p.stream(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
        ):
            events.append(event)

        text_events = [e for e in events if isinstance(e, TextEvent)]
        assert len(text_events) == 2
        assert text_events[0].text == "Hello"
        assert text_events[1].text == " world"
        assert isinstance(events[-1], DoneEvent)

    @pytest.mark.asyncio
    async def test_stream_tool_use_response(self):
        """Test tool use response streaming."""
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        # Create the content block with proper attributes to avoid MagicMock auto-creation
        content_block = MagicMock()
        content_block.type = "tool_use"
        content_block.id = "toolu_1"
        content_block.name = "bash"

        mock_events = [
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=content_block,
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="input_json_delta", partial_json='{"command":'),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="input_json_delta", partial_json=' "ls"}'),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_stop"),
        ]

        # Create an async iterator for the mock stream - __aiter__ receives self
        def mock_aiter(_self):
            async def _aiter():
                for e in mock_events:
                    yield e

            return _aiter()

        # Create a mock stream context manager
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.__aiter__ = mock_aiter

        # Mock the stream method to return the context manager
        p._client.messages.stream = MagicMock(return_value=mock_stream)

        events = []
        async for event in p.stream(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "list files"},
            ],
            tools=[
                ToolSchema(
                    type="function",
                    function={
                        "name": "bash",
                        "description": "Run command",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                        },
                    },
                )
            ],
        ):
            events.append(event)

        tool_events = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_events) == 1
        assert tool_events[0].name == "bash"
        assert tool_events[0].arguments == {"command": "ls"}
        assert tool_events[0].tool_call_id == "toolu_1"


class TestAnthropicUsageExtraction:
    def _make_stream(self, mock_events):
        def mock_aiter(_self):
            async def _aiter():
                for e in mock_events:
                    yield e

            return _aiter()

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.__aiter__ = mock_aiter
        return mock_stream

    @pytest.mark.asyncio
    async def test_message_start_extracts_input_tokens(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        message_usage = MagicMock()
        message_usage.input_tokens = 150
        message_obj = MagicMock()
        message_obj.usage = message_usage

        mock_events = [
            MagicMock(type="message_start", message=message_obj),
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=MagicMock(type="text", text=""),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text="Hi"),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_stop"),
        ]

        p._client.messages.stream = MagicMock(
            return_value=self._make_stream(mock_events)
        )

        events = []
        async for event in p.stream(messages=[{"role": "user", "content": "hi"}]):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].input_tokens == 150

    @pytest.mark.asyncio
    async def test_message_delta_extracts_output_tokens(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        message_usage = MagicMock()
        message_usage.input_tokens = 100

        delta_usage = MagicMock()
        delta_usage.output_tokens = 42

        mock_events = [
            MagicMock(type="message_start", message=MagicMock(usage=message_usage)),
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=MagicMock(type="text", text=""),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text="Hello"),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_delta", usage=delta_usage),
            MagicMock(type="message_stop"),
        ]

        p._client.messages.stream = MagicMock(
            return_value=self._make_stream(mock_events)
        )

        events = []
        async for event in p.stream(messages=[{"role": "user", "content": "hi"}]):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].input_tokens == 100
        assert usage_events[0].output_tokens == 42

    @pytest.mark.asyncio
    async def test_usage_event_yielded_before_done(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        mock_events = [
            MagicMock(
                type="message_start",
                message=MagicMock(usage=MagicMock(input_tokens=50)),
            ),
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=MagicMock(type="text", text=""),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text="ok"),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_delta", usage=MagicMock(output_tokens=25)),
            MagicMock(type="message_stop"),
        ]

        p._client.messages.stream = MagicMock(
            return_value=self._make_stream(mock_events)
        )

        events = []
        async for event in p.stream(messages=[{"role": "user", "content": "hi"}]):
            events.append(event)

        assert isinstance(events[-2], UsageEvent)
        assert isinstance(events[-1], DoneEvent)
        assert events[-2].input_tokens == 50
        assert events[-2].output_tokens == 25

    @pytest.mark.asyncio
    async def test_missing_usage_fields_degrade_to_zeros(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        mock_events = [
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=MagicMock(type="text", text=""),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text="text"),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_stop"),
        ]

        p._client.messages.stream = MagicMock(
            return_value=self._make_stream(mock_events)
        )

        events = []
        async for event in p.stream(messages=[{"role": "user", "content": "hi"}]):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].input_tokens == 0
        assert usage_events[0].output_tokens == 0

    @pytest.mark.asyncio
    async def test_multiple_message_delta_uses_last_output_tokens(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        mock_events = [
            MagicMock(
                type="message_start",
                message=MagicMock(usage=MagicMock(input_tokens=80)),
            ),
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=MagicMock(type="text", text=""),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text="a"),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_delta", usage=MagicMock(output_tokens=10)),
            MagicMock(type="message_delta", usage=MagicMock(output_tokens=30)),
            MagicMock(type="message_stop"),
        ]

        p._client.messages.stream = MagicMock(
            return_value=self._make_stream(mock_events)
        )

        events = []
        async for event in p.stream(messages=[{"role": "user", "content": "hi"}]):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].input_tokens == 80
        assert usage_events[0].output_tokens == 30

    @pytest.mark.asyncio
    async def test_usage_event_has_provider_name(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        mock_events = [
            MagicMock(
                type="message_start",
                message=MagicMock(usage=MagicMock(input_tokens=10)),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="text_delta", text="x"),
            ),
            MagicMock(type="message_stop"),
        ]

        p._client.messages.stream = MagicMock(
            return_value=self._make_stream(mock_events)
        )

        events = []
        async for event in p.stream(messages=[{"role": "user", "content": "hi"}]):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].provider_name == "claude-sonnet-4-20250514"
