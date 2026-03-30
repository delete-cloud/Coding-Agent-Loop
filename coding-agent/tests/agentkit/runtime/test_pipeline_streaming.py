"""Tests for PipelineContext on_event streaming callback."""

import pytest
from typing import Awaitable, Callable, Any
from unittest.mock import AsyncMock, MagicMock
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


# ── Helpers ──────────────────────────────────────────────────────────


class StreamingTestPlugin:
    """Minimal plugin for streaming tests — provides a mock LLM, no tools."""

    state_key = "streaming_test"

    def __init__(self):
        self._mock_llm = MagicMock()

    def hooks(self):
        return {
            "provide_llm": self.provide_llm,
            "provide_storage": self.provide_storage,
            "get_tools": self.get_tools,
            "build_context": self.build_context,
            "summarize_context": self.summarize_context,
        }

    def provide_llm(self, **kwargs):
        return self._mock_llm

    def provide_storage(self, **kwargs):
        return None

    def get_tools(self, **kwargs):
        return []

    def build_context(self, **kwargs):
        return []

    def summarize_context(self, **kwargs):
        return None


@pytest.fixture
def streaming_setup():
    """Create pipeline + plugin wired for streaming tests."""
    registry = PluginRegistry()
    plugin = StreamingTestPlugin()
    registry.register(plugin)
    runtime = HookRuntime(registry)
    pipeline = Pipeline(runtime=runtime, registry=registry)
    return pipeline, plugin


def _make_ctx(on_event=None) -> PipelineContext:
    """Build a PipelineContext with a user message and optional on_event."""
    tape = Tape()
    tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
    return PipelineContext(tape=tape, session_id="test", on_event=on_event)


# ── T1 baseline tests (on_event field) ──────────────────────────────


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
            on_event=None,
        )
        assert ctx.on_event is None
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
        assert ctx.on_event is callback

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
        assert ctx.on_event is callback


# ── T7 tests: on_event is CALLED during run_turn() ──────────────────


class TestStreamingEventEmission:
    """Verify that _stage_run_model emits events through on_event callback."""

    @pytest.mark.asyncio
    async def test_text_event_emitted_to_callback(self, streaming_setup):
        """TextEvent from provider → on_event callback receives it."""
        pipeline, plugin = streaming_setup
        callback = AsyncMock()
        ctx = _make_ctx(on_event=callback)

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="hello")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        await pipeline.run_turn(ctx)

        events = [call.args[0] for call in callback.call_args_list]
        text_events = [e for e in events if isinstance(e, TextEvent)]
        assert len(text_events) == 1
        assert text_events[0].text == "hello"

    @pytest.mark.asyncio
    async def test_tool_call_event_emitted_to_callback(self, streaming_setup):
        """ToolCallEvent from provider → on_event callback receives it."""
        pipeline, plugin = streaming_setup
        callback = AsyncMock()
        ctx = _make_ctx(on_event=callback)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-1", name="file_read", arguments={"path": "x.py"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        plugin._mock_llm.stream = mock_stream

        plugin.hooks()["execute_tool"] = lambda name="", **kw: f"result:{name}"

        registry = PluginRegistry()
        registry.register(plugin)
        runtime = HookRuntime(registry)
        pipeline_with_tools = Pipeline(runtime=runtime, registry=registry)

        await pipeline_with_tools.run_turn(ctx)

        events = [call.args[0] for call in callback.call_args_list]
        tc_events = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tc_events) == 1
        assert tc_events[0].name == "file_read"
        assert tc_events[0].tool_call_id == "tc-1"

    @pytest.mark.asyncio
    async def test_done_event_emitted_to_callback(self, streaming_setup):
        """DoneEvent emitted at end of model call."""
        pipeline, plugin = streaming_setup
        callback = AsyncMock()
        ctx = _make_ctx(on_event=callback)

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="hi")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        await pipeline.run_turn(ctx)

        events = [call.args[0] for call in callback.call_args_list]
        done_events = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done_events) >= 1, "DoneEvent must be emitted via on_event"

    @pytest.mark.asyncio
    async def test_events_emitted_in_correct_order(self, streaming_setup):
        """Multiple events stream in correct order: Text, Text, Done."""
        pipeline, plugin = streaming_setup
        callback = AsyncMock()
        ctx = _make_ctx(on_event=callback)

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="one")
            yield TextEvent(text="two")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream
        await pipeline.run_turn(ctx)

        events = [call.args[0] for call in callback.call_args_list]
        assert len(events) >= 3, f"Expected >=3 events, got {len(events)}: {events}"
        assert isinstance(events[0], TextEvent)
        assert events[0].text == "one"
        assert isinstance(events[1], TextEvent)
        assert events[1].text == "two"
        assert isinstance(events[2], DoneEvent)

    @pytest.mark.asyncio
    async def test_no_on_event_set_no_error(self, streaming_setup):
        """When on_event is None, run_turn succeeds without errors."""
        pipeline, plugin = streaming_setup
        ctx = _make_ctx(on_event=None)

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="hello")
            yield DoneEvent()

        plugin._mock_llm.stream = mock_stream

        result = await pipeline.run_turn(ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_mixed_text_and_tool_events_order(self, streaming_setup):
        """Mixed events: ToolCallEvent then TextEvent in second round, correct order."""
        pipeline, plugin = streaming_setup
        callback = AsyncMock()
        ctx = _make_ctx(on_event=callback)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-1", name="grep", arguments={"pattern": "foo"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="found it")
                yield DoneEvent()

        plugin._mock_llm.stream = mock_stream

        plugin.hooks()["execute_tool"] = lambda name="", **kw: "match found"
        registry = PluginRegistry()
        registry.register(plugin)
        runtime = HookRuntime(registry)
        pipeline_with_tools = Pipeline(runtime=runtime, registry=registry)

        await pipeline_with_tools.run_turn(ctx)

        events = [call.args[0] for call in callback.call_args_list]

        tc_events = [e for e in events if isinstance(e, ToolCallEvent)]
        text_events = [e for e in events if isinstance(e, TextEvent)]
        done_events = [e for e in events if isinstance(e, DoneEvent)]

        assert len(tc_events) == 1, f"Expected 1 ToolCallEvent, got {tc_events}"
        assert len(text_events) == 1, f"Expected 1 TextEvent, got {text_events}"
        assert len(done_events) >= 2, f"Expected >=2 DoneEvents, got {done_events}"

        tc_idx = events.index(tc_events[0])
        text_idx = events.index(text_events[0])
        assert tc_idx < text_idx, "ToolCallEvent should come before TextEvent"
