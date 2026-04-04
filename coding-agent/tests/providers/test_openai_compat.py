"""Tests for OpenAI-compatible provider."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from coding_agent.providers.openai_compat import OpenAICompatProvider
from agentkit.providers.models import (
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    DoneEvent,
    UsageEvent,
)


class TestOpenAICompatProvider:
    def test_supports_custom_default_headers(self):
        provider = OpenAICompatProvider(
            model="gpt-4o",
            api_key="sk-test",
            default_headers={"Accept": "application/vnd.github+json"},
        )

        assert (
            provider._client.default_headers["Accept"] == "application/vnd.github+json"
        )

    def test_context_size_known_model(self):
        provider = OpenAICompatProvider(
            model="gpt-4o",
            api_key="sk-test",
        )
        assert provider.max_context_size == 128000

    def test_context_size_unknown_model_defaults_to_128k(self):
        provider = OpenAICompatProvider(
            model="unknown-model",
            api_key="sk-test",
        )
        assert provider.max_context_size == 128000

    @pytest.mark.asyncio
    async def test_stream_yields_delta_events(self):
        """Test that stream yields delta events for text content."""
        provider = OpenAICompatProvider(
            model="gpt-4o",
            api_key="sk-test",
        )

        # Mock the OpenAI client
        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = "Hello"
        mock_chunk.choices[0].delta.tool_calls = None
        mock_chunk.choices[0].delta.reasoning_content = None
        mock_chunk.choices[0].finish_reason = None
        mock_chunk.usage = None

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_chunk]

        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}]
        ):
            events.append(event)

        assert len(events) == 3  # text + usage + done
        assert isinstance(events[0], TextEvent)
        assert events[0].text == "Hello"
        assert isinstance(events[1], UsageEvent)
        assert isinstance(events[2], DoneEvent)

    @pytest.mark.asyncio
    async def test_stream_handles_rate_limit_with_retry(self):
        """Test that rate limit errors trigger retry."""
        from openai import RateLimitError

        provider = OpenAICompatProvider(
            model="gpt-4o",
            api_key="sk-test",
        )

        # First call raises RateLimitError, second succeeds
        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError(
                    message="Rate limited",
                    response=MagicMock(status_code=429),
                    body=None,
                )

            mock_chunk = MagicMock()
            mock_chunk.choices = [MagicMock()]
            mock_chunk.choices[0].delta.content = "Hello"
            mock_chunk.choices[0].delta.tool_calls = None
            mock_chunk.choices[0].delta.reasoning_content = None
            mock_chunk.choices[0].finish_reason = None
            mock_chunk.usage = None
            mock_chunk.choices[0].usage = None

            mock_stream = AsyncMock()
            mock_stream.__aiter__.return_value = [mock_chunk]
            return mock_stream

        provider._client.chat.completions.create = mock_create

        # Patch asyncio.sleep to avoid waiting in tests
        with patch("asyncio.sleep", new_callable=AsyncMock):
            events = []
            async for event in provider.stream(
                messages=[{"role": "user", "content": "Hi"}]
            ):
                events.append(event)

        # Should have retried once
        assert call_count == 2
        assert isinstance(events[0], TextEvent)
        assert events[0].text == "Hello"

    @pytest.mark.asyncio
    async def test_stream_gives_up_after_max_retries(self):
        """Test that stream gives up after max retries exceeded."""
        from openai import RateLimitError

        provider = OpenAICompatProvider(
            model="gpt-4o",
            api_key="sk-test",
        )

        # Always raise RateLimitError
        async def mock_create(*args, **kwargs):
            raise RateLimitError(
                message="Rate limited",
                response=MagicMock(status_code=429),
                body=None,
            )

        provider._client.chat.completions.create = mock_create

        # Patch asyncio.sleep
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RateLimitError):
                async for event in provider.stream(
                    messages=[{"role": "user", "content": "Hi"}]
                ):
                    pass

    @pytest.mark.asyncio
    async def test_stream_no_retry_on_auth_error(self):
        """Test that auth errors don't trigger retry."""
        from openai import AuthenticationError

        provider = OpenAICompatProvider(
            model="gpt-4o",
            api_key="sk-test",
        )

        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body=None,
            )

        provider._client.chat.completions.create = mock_create

        with pytest.raises(AuthenticationError):
            async for event in provider.stream(
                messages=[{"role": "user", "content": "Hi"}]
            ):
                pass

        # Should not retry
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_stream_yields_tool_calls_on_stop_reason(self):
        """Test that tool calls are yielded even when finish_reason is 'stop'.

        Some APIs (like right.codes) return finish_reason='stop' instead of 'tool_calls'
        when tool calls are present in the stream.
        """
        from coding_agent.providers.base import ToolSchema

        provider = OpenAICompatProvider(
            model="gpt-4o",
            api_key="sk-test",
        )

        # Mock chunks with tool calls and finish_reason="stop"
        mock_chunk1 = MagicMock()
        mock_chunk1.choices = [MagicMock()]
        mock_chunk1.choices[0].delta.content = None
        mock_chunk1.choices[0].delta.reasoning_content = None
        mock_chunk1.choices[0].finish_reason = None
        mock_chunk1.usage = None
        mock_chunk1.choices[0].usage = None

        # Tool call delta
        mock_tool_call = MagicMock()
        mock_tool_call.index = 0
        mock_tool_call.id = "call_123"
        mock_tool_call.function.name = "bash"
        mock_tool_call.function.arguments = '{"command": "ls"}'
        mock_chunk1.choices[0].delta.tool_calls = [mock_tool_call]

        # Final chunk with finish_reason="stop" (not "tool_calls")
        mock_chunk2 = MagicMock()
        mock_chunk2.choices = [MagicMock()]
        mock_chunk2.choices[0].delta.content = None
        mock_chunk2.choices[0].delta.tool_calls = None
        mock_chunk2.choices[0].delta.reasoning_content = None
        mock_chunk2.choices[0].finish_reason = "stop"
        mock_chunk2.usage = None
        mock_chunk2.choices[0].usage = None

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_chunk1, mock_chunk2]

        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        tools = [
            ToolSchema(
                type="function",
                function={
                    "name": "bash",
                    "description": "Execute shell command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            )
        ]

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Run ls"}], tools=tools
        ):
            events.append(event)

        # Should get tool_call + usage + done
        assert len(events) == 3, (
            f"Expected 3 events, got {len(events)}: {[type(e).__name__ for e in events]}"
        )
        assert isinstance(events[0], ToolCallEvent)
        assert events[0].name == "bash"
        assert events[0].arguments == {"command": "ls"}
        assert isinstance(events[1], UsageEvent)
        assert isinstance(events[2], DoneEvent)

    @pytest.mark.asyncio
    async def test_stream_captures_reasoning_content(self):
        provider = OpenAICompatProvider(
            model="kimi-for-coding",
            api_key="sk-test",
        )

        mock_thinking_chunk = MagicMock()
        mock_thinking_chunk.choices = [MagicMock()]
        mock_thinking_chunk.choices[0].delta.content = None
        mock_thinking_chunk.choices[0].delta.reasoning_content = "Let me think..."
        mock_thinking_chunk.choices[0].delta.tool_calls = None
        mock_thinking_chunk.choices[0].finish_reason = None
        mock_thinking_chunk.usage = None
        mock_thinking_chunk.choices[0].usage = None

        mock_text_chunk = MagicMock()
        mock_text_chunk.choices = [MagicMock()]
        mock_text_chunk.choices[0].delta.content = "Here's the answer"
        mock_text_chunk.choices[0].delta.reasoning_content = None
        mock_text_chunk.choices[0].delta.tool_calls = None
        mock_text_chunk.choices[0].finish_reason = None
        mock_text_chunk.usage = None
        mock_text_chunk.choices[0].usage = None

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_thinking_chunk, mock_text_chunk]

        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}]
        ):
            events.append(event)

        assert len(events) == 4  # thinking + text + usage + done
        assert isinstance(events[0], ThinkingEvent)
        assert events[0].text == "Let me think..."
        assert isinstance(events[1], TextEvent)
        assert events[1].text == "Here's the answer"
        assert isinstance(events[2], UsageEvent)
        assert isinstance(events[3], DoneEvent)


class TestOpenAIUsageExtraction:
    @pytest.mark.asyncio
    async def test_usage_event_yielded_before_done(self):
        provider = OpenAICompatProvider(model="gpt-4o", api_key="sk-test")

        mock_text_chunk = MagicMock()
        mock_text_chunk.choices = [MagicMock()]
        mock_text_chunk.choices[0].delta.content = "Hello"
        mock_text_chunk.choices[0].delta.tool_calls = None
        mock_text_chunk.choices[0].delta.reasoning_content = None
        mock_text_chunk.choices[0].finish_reason = None
        mock_text_chunk.usage = None

        mock_usage_chunk = MagicMock()
        mock_usage_chunk.choices = []
        mock_usage_chunk.usage = MagicMock()
        mock_usage_chunk.usage.prompt_tokens = 100
        mock_usage_chunk.usage.completion_tokens = 50

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_text_chunk, mock_usage_chunk]
        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}]
        ):
            events.append(event)

        assert len(events) == 3
        assert isinstance(events[0], TextEvent)
        assert isinstance(events[1], UsageEvent)
        assert events[1].input_tokens == 100
        assert events[1].output_tokens == 50
        assert isinstance(events[2], DoneEvent)

    @pytest.mark.asyncio
    async def test_usage_event_with_no_usage_data_yields_zeros(self):
        provider = OpenAICompatProvider(model="gpt-4o", api_key="sk-test")

        mock_text_chunk = MagicMock()
        mock_text_chunk.choices = [MagicMock()]
        mock_text_chunk.choices[0].delta.content = "Hello"
        mock_text_chunk.choices[0].delta.tool_calls = None
        mock_text_chunk.choices[0].delta.reasoning_content = None
        mock_text_chunk.choices[0].finish_reason = None
        mock_text_chunk.usage = None
        mock_text_chunk.choices[0].usage = None

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_text_chunk]
        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}]
        ):
            events.append(event)

        assert len(events) == 3
        assert isinstance(events[0], TextEvent)
        assert isinstance(events[1], UsageEvent)
        assert events[1].input_tokens == 0
        assert events[1].output_tokens == 0
        assert isinstance(events[2], DoneEvent)

    @pytest.mark.asyncio
    async def test_kimi_non_standard_usage_location(self):
        provider = OpenAICompatProvider(model="kimi-for-coding", api_key="sk-test")

        mock_text_chunk = MagicMock()
        mock_text_chunk.choices = [MagicMock()]
        mock_text_chunk.choices[0].delta.content = "Hello"
        mock_text_chunk.choices[0].delta.tool_calls = None
        mock_text_chunk.choices[0].delta.reasoning_content = None
        mock_text_chunk.choices[0].finish_reason = None
        mock_text_chunk.usage = None
        mock_text_chunk.choices[0].usage = None

        mock_usage_chunk = MagicMock()
        mock_usage_chunk.choices = [MagicMock()]
        mock_usage_chunk.choices[0].delta.content = None
        mock_usage_chunk.choices[0].delta.tool_calls = None
        mock_usage_chunk.choices[0].delta.reasoning_content = None
        mock_usage_chunk.choices[0].finish_reason = "stop"
        mock_usage_chunk.usage = None
        mock_usage_chunk.choices[0].usage = MagicMock()
        mock_usage_chunk.choices[0].usage.prompt_tokens = 200
        mock_usage_chunk.choices[0].usage.completion_tokens = 75

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_text_chunk, mock_usage_chunk]
        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}]
        ):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].input_tokens == 200
        assert usage_events[0].output_tokens == 75

    @pytest.mark.asyncio
    async def test_stream_options_include_usage_is_set(self):
        provider = OpenAICompatProvider(model="gpt-4o", api_key="sk-test")

        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = "Hi"
        mock_chunk.choices[0].delta.tool_calls = None
        mock_chunk.choices[0].delta.reasoning_content = None
        mock_chunk.choices[0].finish_reason = None
        mock_chunk.usage = None

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_chunk]
        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}]
        ):
            events.append(event)

        create_call = provider._client.chat.completions.create
        call_kwargs = create_call.call_args[1]
        assert call_kwargs.get("stream_options") == {"include_usage": True}

    @pytest.mark.asyncio
    async def test_usage_event_has_provider_name(self):
        provider = OpenAICompatProvider(model="gpt-4o", api_key="sk-test")

        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = "Hi"
        mock_chunk.choices[0].delta.tool_calls = None
        mock_chunk.choices[0].delta.reasoning_content = None
        mock_chunk.choices[0].finish_reason = None
        mock_chunk.usage = None

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_chunk]
        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Hi"}]
        ):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].provider_name == "gpt-4o"
