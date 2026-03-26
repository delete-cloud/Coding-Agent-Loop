"""Tests for OpenAI-compatible provider."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from coding_agent.providers.openai_compat import OpenAICompatProvider
from coding_agent.providers.base import StreamEvent


class TestOpenAICompatProvider:
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
        mock_chunk.choices[0].finish_reason = None

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_chunk]

        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        events = []
        async for event in provider.stream(messages=[{"role": "user", "content": "Hi"}]):
            events.append(event)

        assert len(events) == 2  # delta + done
        assert events[0].type == "delta"
        assert events[0].text == "Hello"
        assert events[1].type == "done"

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
            
            # Return successful stream
            mock_chunk = MagicMock()
            mock_chunk.choices = [MagicMock()]
            mock_chunk.choices[0].delta.content = "Hello"
            mock_chunk.choices[0].delta.tool_calls = None
            mock_chunk.choices[0].finish_reason = None
            
            mock_stream = AsyncMock()
            mock_stream.__aiter__.return_value = [mock_chunk]
            return mock_stream

        provider._client.chat.completions.create = mock_create

        # Patch asyncio.sleep to avoid waiting in tests
        with patch("asyncio.sleep", new_callable=AsyncMock):
            events = []
            async for event in provider.stream(messages=[{"role": "user", "content": "Hi"}]):
                events.append(event)

        # Should have retried once
        assert call_count == 2
        assert events[0].type == "delta"
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
            events = []
            async for event in provider.stream(messages=[{"role": "user", "content": "Hi"}]):
                events.append(event)

        # Should get error event
        assert len(events) == 1
        assert events[0].type == "error"
        assert "Rate limit" in events[0].error

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

        events = []
        async for event in provider.stream(messages=[{"role": "user", "content": "Hi"}]):
            events.append(event)

        # Should not retry
        assert call_count == 1
        assert events[0].type == "error"
        assert "API error" in events[0].error

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
        mock_chunk1.choices[0].finish_reason = None
        
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
        mock_chunk2.choices[0].finish_reason = "stop"

        mock_stream = AsyncMock()
        mock_stream.__aiter__.return_value = [mock_chunk1, mock_chunk2]

        provider._client.chat.completions.create = AsyncMock(return_value=mock_stream)

        tools = [ToolSchema(
            type="function",
            function={
                "name": "bash",
                "description": "Execute shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"]
                }
            }
        )]

        events = []
        async for event in provider.stream(
            messages=[{"role": "user", "content": "Run ls"}],
            tools=tools
        ):
            events.append(event)

        # Should get tool_call + done
        assert len(events) == 2, f"Expected 2 events, got {len(events)}: {[e.type for e in events]}"
        assert events[0].type == "tool_call"
        assert events[0].tool_call.name == "bash"
        assert events[0].tool_call.arguments == {"command": "ls"}
        assert events[1].type == "done"
