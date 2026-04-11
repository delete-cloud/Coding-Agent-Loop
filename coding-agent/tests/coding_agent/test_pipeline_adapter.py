from __future__ import annotations

import json
import pytest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from agentkit.errors import PipelineError
from agentkit.providers.models import (
    DoneEvent,
    TextEvent,
    ToolCallEvent,
    ThinkingEvent,
    UsageEvent,
)
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry

from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.plugins.metrics import SessionMetricsPlugin
from coding_agent.wire.protocol import (
    CompletionStatus,
    StreamDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    TurnStatusDelta,
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

    def _execute_tool(self, name: str = "", **kwargs) -> str | dict[str, Any]:
        return f"result:{name}"


class _StructuredResult(dict[str, Any]):
    def __str__(self) -> str:
        return "stdout: ok"


class _StructuredToolPlugin(_MinimalPlugin):
    def _execute_tool(self, name: str = "", **kwargs):
        return _StructuredResult(stdout="ok", stderr="", exit_code=0)


class _MetricsPlugin:
    state_key = "session_metrics"

    def __init__(self) -> None:
        self.plugin = SessionMetricsPlugin()

    def hooks(self):
        return self.plugin.hooks()


def _make_pipeline_and_ctx(
    mock_stream_fn,
    session_id: str = "test-session",
    config: dict[str, Any] | None = None,
    *,
    with_metrics: bool = False,
    plugin_override: _MinimalPlugin | None = None,
) -> tuple[Pipeline, PipelineContext, _MinimalPlugin]:
    plugin = plugin_override or _MinimalPlugin()

    mock_llm = MagicMock()
    mock_llm.stream = mock_stream_fn
    plugin._mock_llm = mock_llm

    registry = PluginRegistry()
    registry.register(plugin)
    if with_metrics:
        registry.register(_MetricsPlugin())
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


class _MountTrackingPlugin(_MinimalPlugin):
    def __init__(self) -> None:
        super().__init__()
        self.mount_calls = 0
        self.shutdown_calls = 0

    def hooks(self):
        hooks: dict[str, Any] = super().hooks()
        hooks["mount"] = self.do_mount
        hooks["on_shutdown"] = self.on_shutdown
        return hooks

    def do_mount(self, **kwargs):
        del kwargs
        self.mount_calls += 1
        return {"mounted": True}

    def on_shutdown(self, **kwargs):
        del kwargs
        self.shutdown_calls += 1


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

    @pytest.mark.asyncio
    async def test_run_turn_mounts_pipeline_once_before_first_turn(self):
        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="mounted")
            yield DoneEvent()

        plugin = _MountTrackingPlugin()
        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, plugin_override=plugin)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        await adapter.run_turn("first")
        await adapter.run_turn("second")

        assert plugin.mount_calls == 1
        assert ctx.plugin_states["minimal"] == {"mounted": True}

    @pytest.mark.asyncio
    async def test_close_triggers_pipeline_shutdown_once(self):
        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="mounted")
            yield DoneEvent()

        plugin = _MountTrackingPlugin()
        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, plugin_override=plugin)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        await adapter.run_turn("first")
        await adapter.close()
        await adapter.close()

        assert plugin.shutdown_calls == 1


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
    async def test_doom_loop_detected_from_session_event_tape_marker(self):
        async def mock_stream(messages: list[dict[str, Any]], tools=None, **kw):
            yield TextEvent(text="stuck")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        original_run_turn = pipeline.run_turn

        async def patched_run_turn(ctx: PipelineContext) -> PipelineContext:
            ctx.tape.append(
                Entry(
                    kind="event",
                    payload={"event_type": "doom_detected", "reason": "doom loop"},
                )
            )
            return await original_run_turn(ctx)

        pipeline.run_turn = cast(Any, patched_run_turn)

        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)
        outcome = await adapter.run_turn("hello")

        assert outcome.stop_reason == StopReason.DOOM_LOOP

    @pytest.mark.asyncio
    async def test_old_doom_event_does_not_poison_later_clean_turn(self):
        async def mock_stream(messages: list[dict[str, Any]], tools=None, **kw):
            yield TextEvent(text="all clear")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "old turn"},
            )
        )
        ctx.tape.append(
            Entry(
                kind="event",
                payload={"event_type": "doom_detected", "reason": "old doom loop"},
            )
        )
        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "old response"},
            )
        )

        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)
        outcome = await adapter.run_turn("new clean turn")

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS

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
    async def test_last_assistant_message_after_tool_calls_means_completed_even_with_later_entries(
        self,
    ):
        async def mock_stream(messages, tools=None, **kw):
            yield ToolCallEvent(
                tool_call_id="tc-subagent",
                name="subagent",
                arguments={"goal": "delegate"},
            )
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "delegate work"},
            )
        )
        ctx.tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "id": "tc-subagent",
                    "name": "subagent",
                    "arguments": {"goal": "delegate"},
                    "role": "assistant",
                },
            )
        )
        ctx.tape.append(
            Entry(
                kind="tool_result",
                payload={
                    "tool_call_id": "tc-subagent",
                    "content": "Subagent completed: Child finished summary",
                },
            )
        )
        ctx.tape.append(
            Entry(
                kind="message",
                payload={
                    "role": "assistant",
                    "content": "Parent received child result",
                },
            )
        )
        ctx.tape.append(
            Entry(
                kind="event",
                payload={"event_type": "topic_start", "summary": "next turn anchor"},
            )
        )

        assert adapter._determine_stop_reason() == StopReason.NO_TOOL_CALLS

    @pytest.mark.asyncio
    async def test_hidden_child_entries_do_not_mark_parent_turn_completed(self):
        async def mock_stream(messages, tools=None, **kw):
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        ctx.tape.append(
            Entry(kind="message", payload={"role": "user", "content": "delegate work"})
        )
        ctx.tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "id": "tc-subagent",
                    "name": "subagent",
                    "arguments": {"goal": "delegate"},
                    "role": "assistant",
                },
            )
        )
        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "child finished"},
                meta={"skip_context": True},
            )
        )

        assert adapter._determine_stop_reason() == StopReason.MAX_STEPS_REACHED

    @pytest.mark.asyncio
    async def test_hidden_child_entries_do_not_override_parent_final_message(self):
        async def mock_stream(messages, tools=None, **kw):
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "parent result"},
            )
        )
        ctx.tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "hidden child result"},
                meta={"skip_context": True},
            )
        )

        assert adapter._extract_final_message() == "parent result"

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
        assert deltas[0].agent_id == ""

    @pytest.mark.asyncio
    async def test_child_consumer_emits_agent_id_on_wire_messages(self):
        async def mock_stream(messages, tools=None, **kw):
            yield ThinkingEvent(text="thinking")
            yield ToolCallEvent(
                tool_call_id="tc-child",
                name="bash_run",
                arguments={"command": "pwd"},
            )
            yield TextEvent(text="child says hi")
            yield UsageEvent(
                input_tokens=11,
                output_tokens=7,
                provider_name="test-provider",
            )
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(
            mock_stream, session_id="parent-session"
        )
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(
            pipeline=pipeline,
            ctx=ctx,
            consumer=consumer,
            agent_id="child-agent-1",
        )

        await adapter.run_turn("delegate")

        assert consumer.messages
        assert {message.agent_id for message in consumer.messages} == {"child-agent-1"}
        assert {message.session_id for message in consumer.messages} == {
            "parent-session"
        }

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

    @pytest.mark.asyncio
    async def test_tool_result_stays_string_when_structured_results_disabled(self):
        call_count = 0

        async def mock_stream(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-structured-off",
                    name="bash_run",
                    arguments={"command": "echo ok"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(
            mock_stream,
            session_id="s-structured-off",
            plugin_override=_StructuredToolPlugin(),
        )
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("run tool")

        tool_results = [m for m in consumer.messages if isinstance(m, ToolResultDelta)]
        assert len(tool_results) == 1
        tool_result = tool_results[0]
        assert tool_result.result == "stdout: ok"
        assert tool_result.display_result == "stdout: ok"
        assert ctx.tape.filter("tool_result")[0].payload["content"] == "stdout: ok"

    @pytest.mark.asyncio
    async def test_tool_result_keeps_dict_when_structured_results_enabled(self):
        call_count = 0

        async def mock_stream(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-structured-on",
                    name="bash_run",
                    arguments={"command": "echo ok"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(
            mock_stream,
            session_id="s-structured-on",
            config={"structured_results": True},
            plugin_override=_StructuredToolPlugin(),
        )
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("run tool")

        tool_results = [m for m in consumer.messages if isinstance(m, ToolResultDelta)]
        assert len(tool_results) == 1
        tool_result = tool_results[0]
        assert tool_result.result == {"stdout": "ok", "stderr": "", "exit_code": 0}
        assert tool_result.display_result == "stdout: ok"
        assert ctx.tape.filter("tool_result")[0].payload["content"] == json.dumps(
            {"stdout": "ok", "stderr": "", "exit_code": 0}
        )


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

    @pytest.mark.asyncio
    async def test_reasoning_only_second_round_after_tool_call_returns_no_visible_message(
        self,
    ):
        call_count = 0

        async def mock_stream(messages, tools=None, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1",
                    name="skill_invoke",
                    arguments={"name": "using-superpowers"},
                )
                yield DoneEvent()
            else:
                yield ThinkingEvent(text="Let me think...")
                yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream)
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx)

        outcome = await adapter.run_turn("hello")

        assert outcome.final_message is None


class TestErrorRecovery:
    """T12: REPL-safe error recovery — PipelineAdapter must never crash the REPL."""

    def _make_adapter_with_mock_pipeline(
        self,
        side_effect,
        session_id: str = "err-session",
        consumer=None,
    ) -> tuple[PipelineAdapter, PipelineContext, AsyncMock]:
        """Build adapter with a mock Pipeline whose run_turn raises `side_effect`."""
        tape = Tape()
        ctx = PipelineContext(tape=tape, session_id=session_id, config={})
        mock_pipeline = AsyncMock(spec=Pipeline)
        mock_pipeline._directive_executor = None
        mock_pipeline.run_turn = AsyncMock(side_effect=side_effect)
        adapter = PipelineAdapter(pipeline=mock_pipeline, ctx=ctx, consumer=consumer)
        return adapter, ctx, mock_pipeline

    # ── 1. PipelineError → TurnOutcome(ERROR) ───────────────────────────

    @pytest.mark.asyncio
    async def test_pipeline_error_returns_error_outcome(self):
        """PipelineError → TurnOutcome(stop_reason=ERROR, error=message)."""
        adapter, ctx, _ = self._make_adapter_with_mock_pipeline(
            PipelineError("stage blew up", stage="run_model")
        )

        outcome = await adapter.run_turn("hello")

        assert outcome.stop_reason == StopReason.ERROR
        assert outcome.error is not None
        assert "stage blew up" in outcome.error

    # ── 2. User message preserved after Pipeline rollback ────────────────

    @pytest.mark.asyncio
    async def test_user_message_preserved_after_pipeline_rollback(self):
        """Pipeline rolls back tape, but adapter re-appends user message."""
        tape = Tape()
        ctx = PipelineContext(tape=tape, session_id="s-rollback", config={})

        async def rollback_side_effect(ctx_arg):
            """Simulate Pipeline.run_turn rollback: replace tape with empty one."""
            ctx_arg.tape = Tape()
            raise PipelineError("kaboom", stage="build_context")

        mock_pipeline = AsyncMock(spec=Pipeline)
        mock_pipeline.run_turn = AsyncMock(side_effect=rollback_side_effect)
        adapter = PipelineAdapter(pipeline=mock_pipeline, ctx=ctx)

        outcome = await adapter.run_turn("important question")

        assert outcome.stop_reason == StopReason.ERROR
        user_entries = [
            e
            for e in ctx.tape
            if e.kind == "message" and e.payload.get("role") == "user"
        ]
        assert len(user_entries) == 1
        assert user_entries[0].payload["content"] == "important question"

    # ── 3. KeyboardInterrupt → INTERRUPTED, not crash ────────────────────

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_returns_interrupted(self):
        """KeyboardInterrupt → TurnOutcome(stop_reason=INTERRUPTED)."""
        adapter, ctx, _ = self._make_adapter_with_mock_pipeline(KeyboardInterrupt())

        outcome = await adapter.run_turn("long task")

        assert outcome.stop_reason == StopReason.INTERRUPTED
        assert outcome.error is None or "interrupt" in outcome.error.lower()

    # ── 4. Generic RuntimeError → TurnOutcome(ERROR) ────────────────────

    @pytest.mark.asyncio
    async def test_generic_runtime_error_returns_error_outcome(self):
        """RuntimeError → TurnOutcome(stop_reason=ERROR)."""
        adapter, ctx, _ = self._make_adapter_with_mock_pipeline(
            RuntimeError("unexpected failure")
        )

        outcome = await adapter.run_turn("do stuff")

        assert outcome.stop_reason == StopReason.ERROR
        assert outcome.error is not None
        assert "unexpected failure" in outcome.error

    # ── 5. Multiple consecutive errors → adapter stays functional ────────

    @pytest.mark.asyncio
    async def test_multiple_consecutive_errors_adapter_stays_functional(self):
        """Multiple run_turn errors don't break the adapter."""
        tape = Tape()
        ctx = PipelineContext(tape=tape, session_id="s-multi", config={})

        call_count = 0

        async def always_error(ctx_arg):
            nonlocal call_count
            call_count += 1
            raise PipelineError(f"error #{call_count}", stage="run_model")

        mock_pipeline = AsyncMock(spec=Pipeline)
        mock_pipeline.run_turn = AsyncMock(side_effect=always_error)
        adapter = PipelineAdapter(pipeline=mock_pipeline, ctx=ctx)

        o1 = await adapter.run_turn("msg1")
        assert o1.stop_reason == StopReason.ERROR
        assert o1.error is not None
        assert "error #1" in o1.error

        o2 = await adapter.run_turn("msg2")
        assert o2.stop_reason == StopReason.ERROR
        assert o2.error is not None
        assert "error #2" in o2.error

        o3 = await adapter.run_turn("msg3")
        assert o3.stop_reason == StopReason.ERROR
        assert o3.error is not None
        assert "error #3" in o3.error

        user_entries = [
            e
            for e in ctx.tape
            if e.kind == "message" and e.payload.get("role") == "user"
        ]
        assert len(user_entries) == 3

    # ── 6. TurnEnd with ERROR emitted to consumer on error ───────────────

    @pytest.mark.asyncio
    async def test_error_emits_turn_end_error_to_consumer(self):
        """On error, TurnEnd(completion_status=ERROR) emitted to consumer."""
        consumer = _RecordingConsumer()
        adapter, ctx, _ = self._make_adapter_with_mock_pipeline(
            PipelineError("consumer test", stage="render"),
            session_id="s-consumer-err",
            consumer=consumer,
        )

        await adapter.run_turn("hello")

        turn_ends = [m for m in consumer.messages if isinstance(m, TurnEnd)]
        assert len(turn_ends) == 1
        assert turn_ends[0].completion_status == CompletionStatus.ERROR
        assert turn_ends[0].session_id == "s-consumer-err"


class TestThinkingAndUsageEventHandling:
    @pytest.mark.asyncio
    async def test_thinking_event_emits_thinking_delta(self):
        async def mock_stream(messages, tools=None, **kw):
            yield ThinkingEvent(text="Let me reason...")
            yield TextEvent(text="Answer")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s-think")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("question")

        thinking_deltas = [m for m in consumer.messages if isinstance(m, ThinkingDelta)]
        assert len(thinking_deltas) == 1
        assert thinking_deltas[0].text == "Let me reason..."
        assert thinking_deltas[0].session_id == "s-think"

    @pytest.mark.asyncio
    async def test_usage_event_emits_turn_status_delta(self):
        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="Hi")
            yield UsageEvent(input_tokens=100, output_tokens=50, provider_name="gpt-4o")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s-usage")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("hello")

        status_deltas = [m for m in consumer.messages if isinstance(m, TurnStatusDelta)]
        assert len(status_deltas) == 1
        assert status_deltas[0].tokens_in == 100
        assert status_deltas[0].tokens_out == 50
        assert status_deltas[0].phase == "idle"
        assert status_deltas[0].session_id == "s-usage"

    @pytest.mark.asyncio
    async def test_usage_event_updates_session_metrics_plugin(self):
        async def mock_stream(messages, tools=None, **kw):
            yield UsageEvent(input_tokens=11, output_tokens=7, provider_name="gpt-4o")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(
            mock_stream, session_id="s-metrics", with_metrics=True
        )
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("hello")

        plugin_state = ctx.plugin_states.get("session_metrics", {})
        assert plugin_state.get("tokens_input") == 11
        assert plugin_state.get("tokens_output") == 7

    @pytest.mark.asyncio
    async def test_mixed_event_stream_converts_in_order(self):
        async def mock_stream(messages, tools=None, **kw):
            yield ThinkingEvent(text="hmm")
            yield TextEvent(text="result")
            yield UsageEvent(input_tokens=10, output_tokens=5, provider_name="test")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s-mix")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("test")

        msgs_before_turn_end = [
            m for m in consumer.messages if not isinstance(m, TurnEnd)
        ]
        assert isinstance(msgs_before_turn_end[0], ThinkingDelta)
        assert isinstance(msgs_before_turn_end[1], StreamDelta)
        assert isinstance(msgs_before_turn_end[2], TurnStatusDelta)

    @pytest.mark.asyncio
    async def test_usage_event_with_zero_tokens_still_emits(self):
        async def mock_stream(messages, tools=None, **kw):
            yield TextEvent(text="text")
            yield UsageEvent(input_tokens=0, output_tokens=0, provider_name="test")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s-zero")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("hi")

        status_deltas = [m for m in consumer.messages if isinstance(m, TurnStatusDelta)]
        assert len(status_deltas) == 1
        assert status_deltas[0].tokens_in == 0
        assert status_deltas[0].tokens_out == 0

    @pytest.mark.asyncio
    async def test_thinking_interleaved_with_text(self):
        async def mock_stream(messages, tools=None, **kw):
            yield ThinkingEvent(text="first thought")
            yield TextEvent(text="partial")
            yield ThinkingEvent(text="second thought")
            yield TextEvent(text="done")
            yield DoneEvent()

        pipeline, ctx, _ = _make_pipeline_and_ctx(mock_stream, session_id="s-inter")
        consumer = _RecordingConsumer()
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter.run_turn("complex")

        msgs_before_end = [m for m in consumer.messages if not isinstance(m, TurnEnd)]
        assert isinstance(msgs_before_end[0], ThinkingDelta)
        assert msgs_before_end[0].text == "first thought"
        assert isinstance(msgs_before_end[1], StreamDelta)
        assert msgs_before_end[1].content == "partial"
        assert isinstance(msgs_before_end[2], ThinkingDelta)
        assert msgs_before_end[2].text == "second thought"
        assert isinstance(msgs_before_end[3], StreamDelta)
        assert msgs_before_end[3].content == "done"
