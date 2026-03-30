"""Tests for StreamEvent → agentkit event adapter."""

import pytest
from typing import AsyncIterator

from coding_agent.providers.base import StreamEvent, ToolCall
from coding_agent.plugins.llm_provider import adapt_stream_events
from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent


@pytest.fixture
def mock_old_stream_delta() -> AsyncIterator[StreamEvent]:
    """Mock old provider stream with delta event."""

    async def stream():
        yield StreamEvent(type="delta", text="hello")
        yield StreamEvent(type="delta", text=" world")
        yield StreamEvent(type="done")

    return stream()


@pytest.fixture
def mock_old_stream_tool_call() -> AsyncIterator[StreamEvent]:
    """Mock old provider stream with tool_call event."""

    async def stream():
        yield StreamEvent(
            type="tool_call",
            tool_call=ToolCall(
                id="call_123",
                name="grep",
                arguments={"pattern": "error", "path": "src/"},
            ),
        )
        yield StreamEvent(type="done")

    return stream()


@pytest.fixture
def mock_old_stream_error() -> AsyncIterator[StreamEvent]:
    """Mock old provider stream with error event."""

    async def stream():
        yield StreamEvent(type="error", error="API rate limit exceeded")

    return stream()


class TestAdaptStreamEventsDelta:
    @pytest.mark.asyncio
    async def test_delta_to_text_event(self):
        """delta StreamEvent converts to TextEvent."""

        async def old_stream():
            yield StreamEvent(type="delta", text="hello")

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], TextEvent)
        assert events[0].text == "hello"
        assert events[0].kind == "text"

    @pytest.mark.asyncio
    async def test_delta_empty_text(self):
        """delta with None text converts to TextEvent with empty string."""

        async def old_stream():
            yield StreamEvent(type="delta", text=None)

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], TextEvent)
        assert events[0].text == ""

    @pytest.mark.asyncio
    async def test_multiple_deltas(self):
        """Multiple delta events yield multiple TextEvents."""

        async def old_stream():
            yield StreamEvent(type="delta", text="hello")
            yield StreamEvent(type="delta", text=" ")
            yield StreamEvent(type="delta", text="world")

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 3
        assert all(isinstance(e, TextEvent) for e in events)
        assert [e.text for e in events] == ["hello", " ", "world"]


class TestAdaptStreamEventsToolCall:
    @pytest.mark.asyncio
    async def test_tool_call_event(self):
        """tool_call StreamEvent converts to ToolCallEvent."""

        async def old_stream():
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="call_456", name="file_read", arguments={"path": "utils.py"}
                ),
            )

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], ToolCallEvent)
        assert events[0].tool_call_id == "call_456"
        assert events[0].name == "file_read"
        assert events[0].arguments == {"path": "utils.py"}
        assert events[0].kind == "tool_call"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self):
        """Multiple tool_call events yield multiple ToolCallEvents."""

        async def old_stream():
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="1", name="grep", arguments={"pattern": "error"}),
            )
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id="2", name="file_read", arguments={"path": "src/"}
                ),
            )

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 2
        assert all(isinstance(e, ToolCallEvent) for e in events)
        assert [e.tool_call_id for e in events] == ["1", "2"]
        assert [e.name for e in events] == ["grep", "file_read"]


class TestAdaptStreamEventsDone:
    @pytest.mark.asyncio
    async def test_done_event(self):
        """done StreamEvent converts to DoneEvent."""

        async def old_stream():
            yield StreamEvent(type="done")

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], DoneEvent)
        assert events[0].kind == "done"

    @pytest.mark.asyncio
    async def test_mixed_delta_and_done(self):
        """Sequence of delta then done events."""

        async def old_stream():
            yield StreamEvent(type="delta", text="response")
            yield StreamEvent(type="done")

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 2
        assert isinstance(events[0], TextEvent)
        assert isinstance(events[1], DoneEvent)


class TestAdaptStreamEventsError:
    @pytest.mark.asyncio
    async def test_error_event_yields_done(self):
        """error StreamEvent converts to DoneEvent (errors don't have error field)."""

        async def old_stream():
            yield StreamEvent(type="error", error="API rate limit exceeded")

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        # Error events yield a DoneEvent (no error field in agentkit DoneEvent)
        # The error is logged/handled differently
        assert len(events) == 1
        assert isinstance(events[0], DoneEvent)
        assert events[0].kind == "done"

    @pytest.mark.asyncio
    async def test_complex_stream_sequence(self):
        """Complex sequence: delta → tool_call → delta → done."""

        async def old_stream():
            yield StreamEvent(type="delta", text="thinking... ")
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="1", name="read", arguments={"path": "x.py"}),
            )
            yield StreamEvent(type="delta", text="done")
            yield StreamEvent(type="done")

        events = []
        async for event in adapt_stream_events(old_stream()):
            events.append(event)

        assert len(events) == 4
        assert isinstance(events[0], TextEvent)
        assert isinstance(events[1], ToolCallEvent)
        assert isinstance(events[2], TextEvent)
        assert isinstance(events[3], DoneEvent)
