from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry

from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.wire.protocol import (
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)


class _MinimalPlugin:
    state_key = "minimal"

    def __init__(self) -> None:
        self._mock_llm = MagicMock()

    def hooks(self):
        return {
            "provide_llm": lambda **kw: self._mock_llm,
            "provide_storage": lambda **kw: None,
            "get_tools": lambda **kw: [],
            "build_context": lambda **kw: [],
            "summarize_context": lambda **kw: None,
            "execute_tool": self._execute_tool,
        }

    def _execute_tool(self, name: str = "", **kwargs):
        return f"result:{name}"


def _make_pipeline_and_ctx(
    mock_stream_fn,
    session_id: str = "test-session",
    config: dict | None = None,
) -> tuple[Pipeline, PipelineContext, _MinimalPlugin]:
    plugin = _MinimalPlugin()

    mock_llm = MagicMock()
    mock_llm.stream = mock_stream_fn
    plugin._mock_llm = mock_llm

    registry = PluginRegistry()
    registry.register(plugin)
    runtime = HookRuntime(registry)
    pipeline = Pipeline(runtime=runtime, registry=registry)

    tape = Tape()
    ctx = PipelineContext(
        tape=tape,
        session_id=session_id,
        config=config or {},
    )
    return pipeline, ctx, plugin


class _RecordingConsumer:
    def __init__(self) -> None:
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req):
        from coding_agent.wire.protocol import ApprovalResponse

        return ApprovalResponse(
            session_id=req.session_id, request_id=req.request_id, approved=True
        )


class TestRunTurnReturnsOutcome:
    @pytest.mark.asyncio
    async def test_simple_text_response_returns_no_tool_calls(self):
        """run_turn("hello") → TurnOutcome(stop_reason=NO_TOOL_CALLS)."""

        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="Hi there!")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("hello")

        assert isinstance(outcome, TurnOutcome)
        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS

    @pytest.mark.asyncio
    async def test_final_message_extracted(self):
        """final_message is the last assistant text on the tape."""

        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="The answer is 42.")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("what is the answer?")
        assert outcome.final_message == "The answer is 42."

    @pytest.mark.asyncio
    async def test_user_message_appended_to_tape(self):
        """run_turn appends a user Entry before calling pipeline."""

        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="ok")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)
        await adapter.run_turn("ping")

        user_entries = ctx.tape.filter("message")
        assert any(
            e.payload.get("role") == "user" and e.payload.get("content") == "ping"
            for e in user_entries
        )

    @pytest.mark.asyncio
    async def test_steps_taken_counts_tool_rounds(self):
        """steps_taken reflects number of tool_call entries."""

        call_count = 0

        async def mock_stream(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="grep", arguments={"q": "x"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("search for x")
        assert outcome.steps_taken == 1

    @pytest.mark.asyncio
    async def test_steps_taken_counts_multiple_tool_calls(self):
        call_count = 0

        async def mock_stream(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="file_read", arguments={"path": "a.txt"}
                )
                yield ToolCallEvent(
                    tool_call_id="tc2", name="file_read", arguments={"path": "b.txt"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("read both files")
        assert outcome.steps_taken == 2


class TestStopReasonMapping:
    @pytest.mark.asyncio
    async def test_doom_loop_detected(self):
        """When doom_detector sets doom_detected=True → DOOM_LOOP."""

        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="stuck")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        ctx.plugin_states["doom_detector"] = {"doom_detected": True}

        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)
        outcome = await adapter.run_turn("hello")

        assert outcome.stop_reason == StopReason.DOOM_LOOP

    @pytest.mark.asyncio
    async def test_max_steps_reached(self):
        """When tool rounds exhaust max_tool_rounds → MAX_STEPS_REACHED."""

        async def always_tool_call(messages, tools=None, **kw):
            yield ToolCallEvent(tool_call_id="tc", name="grep", arguments={"q": "x"})
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(
            always_tool_call,
            config={"max_tool_rounds": 2},
        )
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("loop forever")
        assert outcome.stop_reason == StopReason.MAX_STEPS_REACHED

    @pytest.mark.asyncio
    async def test_error_on_pipeline_exception(self):
        """Pipeline exception → TurnOutcome(stop_reason=ERROR, error=...)."""

        async def exploding_stream(messages, tools=None, **kw):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        pipeline, ctx, _ = _make_pipeline_and_ctx(exploding_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("hello")
        assert outcome.stop_reason == StopReason.ERROR
        assert outcome.error is not None
        assert "boom" in outcome.error


class TestEventToWireMessage:
    @pytest.mark.asyncio
    async def test_text_event_emits_stream_delta(self):
        """TextEvent → StreamDelta WireMessage emitted to consumer."""

        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="Hello!")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s1")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("hi")

        deltas = [m for m in consumer.messages if isinstance(m, StreamDelta)]
        assert len(deltas) >= 1
        assert deltas[0].content == "Hello!"
        assert deltas[0].session_id == "s1"

    @pytest.mark.asyncio
    async def test_tool_call_event_emits_tool_call_delta(self):
        """ToolCallEvent → ToolCallDelta WireMessage emitted."""

        call_count = 0

        async def mock_stream(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-42",
                    name="file_read",
                    arguments={"path": "/tmp/x.py"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="contents")
                yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s2")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("read file")

        tc_deltas = [m for m in consumer.messages if isinstance(m, ToolCallDelta)]
        assert len(tc_deltas) >= 1
        assert tc_deltas[0].tool_name == "file_read"
        assert tc_deltas[0].arguments == {"path": "/tmp/x.py"}
        assert tc_deltas[0].call_id == "tc-42"
        assert tc_deltas[0].session_id == "s2"

    @pytest.mark.asyncio
    async def test_turn_end_emitted_after_run_turn_not_on_done_event(self):
        """TurnEnd is emitted AFTER run_turn completes, NOT on DoneEvent."""

        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="hi")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s3")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("hello")

        turn_ends = [m for m in consumer.messages if isinstance(m, TurnEnd)]
        assert len(turn_ends) == 1
        assert turn_ends[0].session_id == "s3"
        assert turn_ends[0].completion_status == CompletionStatus.COMPLETED

        # TurnEnd must be the LAST message emitted
        assert isinstance(consumer.messages[-1], TurnEnd)

    @pytest.mark.asyncio
    async def test_turn_end_blocked_on_max_steps(self):
        """TurnEnd completion_status=BLOCKED when max steps reached."""

        async def always_tool_call(messages, tools=None, **kw):
            yield ToolCallEvent(tool_call_id="tc", name="grep", arguments={"q": "x"})
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(
            always_tool_call,
            session_id="s4",
            config={"max_tool_rounds": 2},
        )
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("loop")

        turn_ends = [m for m in consumer.messages if isinstance(m, TurnEnd)]
        assert len(turn_ends) == 1
        assert turn_ends[0].completion_status == CompletionStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_turn_end_error_on_exception(self):
        """TurnEnd completion_status=ERROR when pipeline errors."""

        async def exploding_stream(messages, tools=None, **kw):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

        pipeline, ctx, _ = _make_pipeline_and_ctx(exploding_stream, session_id="s5")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        outcome = await adapter.run_turn("hello")

        turn_ends = [m for m in consumer.messages if isinstance(m, TurnEnd)]
        assert len(turn_ends) == 1
        assert turn_ends[0].completion_status == CompletionStatus.ERROR


class TestNoConsumer:
    @pytest.mark.asyncio
    async def test_no_consumer_no_crash(self):
        """Adapter works fine without a consumer (events silently dropped)."""

        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="Hello!")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=None)

        outcome = await adapter.run_turn("hello")
        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message == "Hello!"

    @pytest.mark.asyncio
    async def test_no_consumer_with_tool_calls(self):
        """Tool-call round works without consumer."""

        call_count = 0

        async def mock_stream(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="file_read", arguments={"path": "x"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("read x")
        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.steps_taken == 1
