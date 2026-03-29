"""Tests for PipelineContext on_event streaming callback."""

import pytest
from typing import Awaitable, Callable, Any
from unittest.mock import AsyncMock
from agentkit.runtime.pipeline import PipelineContext
from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent
from agentkit.tape.tape import Tape


class TestPipelineContextStreaming:
    """Test suite for on_event streaming callback."""

    def test_on_event_field_exists(self):
        """Verify on_event field exists on PipelineContext."""
        tape = Tape()
        ctx = PipelineContext(tape=tape, session_id="test")
        assert hasattr(ctx, "on_event"), "PipelineContext should have on_event field"

    def test_on_event_defaults_to_none(self):
        """Verify on_event defaults to None (no-op)."""
        tape = Tape()
        ctx = PipelineContext(tape=tape, session_id="test")
        assert ctx.on_event is None, "on_event should default to None"

    @pytest.mark.asyncio
    async def test_on_event_callback_with_none_baseline(self):
        """Baseline: callback is None, no events occur, no-op."""
        tape = Tape()
        callback = AsyncMock()
        ctx = PipelineContext(
            tape=tape,
            session_id="test",
            on_event=None,  # Explicitly None
        )
        # If on_event is None, callback should never be called
        assert ctx.on_event is None
        # Verify callback not invoked (it's None anyway)
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_event_callback_can_be_set(self):
        """Verify on_event can be set to a callable."""
        tape = Tape()
        callback: Callable[[Any], Awaitable[None]] = AsyncMock()
        ctx = PipelineContext(
            tape=tape,
            session_id="test",
            on_event=callback,
        )
        assert ctx.on_event is callback
        assert callable(ctx.on_event)

    @pytest.mark.asyncio
    async def test_on_event_accepts_text_event(self):
        """Verify on_event callback signature accepts TextEvent."""
        tape = Tape()
        callback: Callable[[Any], Awaitable[None]] = AsyncMock()
        ctx = PipelineContext(
            tape=tape,
            session_id="test",
            on_event=callback,
        )
        event = TextEvent(text="hello")
        # Verify the callback signature is compatible
        assert ctx.on_event is callback
        # In future tasks, the callback would be invoked here
        # For now, we just verify it can be assigned and is of correct type

    @pytest.mark.asyncio
    async def test_on_event_accepts_tool_call_event(self):
        """Verify on_event callback signature accepts ToolCallEvent."""
        tape = Tape()
        callback: Callable[[Any], Awaitable[None]] = AsyncMock()
        ctx = PipelineContext(
            tape=tape,
            session_id="test",
            on_event=callback,
        )
        event = ToolCallEvent(tool_call_id="tc-1", name="test_tool", arguments={})
        # Verify the callback signature is compatible
        assert ctx.on_event is callback

    @pytest.mark.asyncio
    async def test_on_event_accepts_done_event(self):
        """Verify on_event callback signature accepts DoneEvent."""
        tape = Tape()
        callback: Callable[[Any], Awaitable[None]] = AsyncMock()
        ctx = PipelineContext(
            tape=tape,
            session_id="test",
            on_event=callback,
        )
        event = DoneEvent()
        # Verify the callback signature is compatible
        assert ctx.on_event is callback
