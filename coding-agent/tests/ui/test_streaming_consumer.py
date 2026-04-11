import asyncio
import time
from unittest.mock import MagicMock

import pytest
from io import StringIO
from rich.console import Console

from coding_agent.ui.stream_renderer import StreamingRenderer
from coding_agent.ui.rich_consumer import RichConsumer
from coding_agent.wire.protocol import (
    StreamDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    TurnStatusDelta,
    CompletionStatus,
)


class TestStreamingConsumer:
    def _make_consumer(self) -> tuple[RichConsumer, StreamingRenderer, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        consumer = RichConsumer(renderer)
        return consumer, renderer, buf

    @pytest.mark.asyncio
    async def test_stream_delta_renders_text(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(StreamDelta(content="Hello"))
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        assert "Hello" in buf.getvalue()

    @pytest.mark.asyncio
    async def test_tool_call_delta_renders_panel(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(tool_name="bash", arguments={"command": "ls"}, call_id="c1")
        )
        output = buf.getvalue()
        assert "bash" in output

    @pytest.mark.asyncio
    async def test_tool_result_delta_renders(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(tool_name="bash", arguments={"command": "ls"}, call_id="c1")
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="bash", result="file.py")
        )
        output = buf.getvalue()
        assert "file.py" in output

    @pytest.mark.asyncio
    async def test_tool_result_delta_prefers_display_result_string(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(tool_name="bash", arguments={"command": "ls"}, call_id="c1")
        )
        await consumer.emit(
            ToolResultDelta(
                call_id="c1",
                tool_name="bash",
                result={"stdout": "file.py", "exit_code": 0},
                display_result="file.py",
            )
        )
        output = buf.getvalue()
        assert "file.py" in output
        assert "stdout" not in output

    @pytest.mark.asyncio
    async def test_child_stream_delta_is_prefixed(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(StreamDelta(agent_id="child-7", content="Hello from child"))
        await consumer.emit(
            TurnEnd(
                agent_id="child-7",
                turn_id="t-child",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

        output = buf.getvalue()
        assert "[child-7]" in output
        assert "Hello from child" in output

    @pytest.mark.asyncio
    async def test_child_tool_activity_is_prefixed(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                agent_id="child-9",
                tool_name="bash_run",
                arguments={"command": "ls"},
                call_id="c1",
            )
        )
        await consumer.emit(
            ToolResultDelta(
                agent_id="child-9",
                call_id="c1",
                tool_name="bash_run",
                result="file.py",
            )
        )

        output = buf.getvalue()
        assert "[child-9] bash_run" in output
        assert "file.py" in output

    @pytest.mark.asyncio
    async def test_turn_end_completed_no_jargon(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "StopReason" not in output

    @pytest.mark.asyncio
    async def test_turn_end_error(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.ERROR)
        )
        output = buf.getvalue()
        assert "error" in output.lower()

    @pytest.mark.asyncio
    async def test_first_stream_delta_starts_stream(self):
        consumer, renderer, _ = self._make_consumer()
        assert not renderer._in_stream
        await consumer.emit(StreamDelta(content="text"))
        assert renderer._in_stream

    @pytest.mark.asyncio
    async def test_tool_call_ends_active_stream(self):
        consumer, renderer, _ = self._make_consumer()
        await consumer.emit(StreamDelta(content="analyzing..."))
        assert renderer._in_stream
        assert consumer._stream_active
        await consumer.emit(ToolCallDelta(tool_name="bash", arguments={}, call_id="c1"))
        assert not consumer._stream_active

    @pytest.mark.asyncio
    async def test_turn_end_ends_active_stream(self):
        consumer, renderer, _ = self._make_consumer()
        await consumer.emit(StreamDelta(content="text"))
        assert consumer._stream_active
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        assert not consumer._stream_active
        assert not renderer._in_stream

    @pytest.mark.asyncio
    async def test_approval_auto_approves(self, monkeypatch):
        from coding_agent.wire.protocol import ApprovalRequest
        from coding_agent.ui import approval_prompt

        consumer, _, _ = self._make_consumer()

        consumer._session_approved_tools.add("bash")

        req = ApprovalRequest(
            session_id="s1", request_id="r1", tool="bash", args={"command": "rm -rf /"}
        )
        resp = await consumer.request_approval(req)
        assert resp.approved is True

    @pytest.mark.asyncio
    async def test_approval_shows_preview_and_prompts(self, monkeypatch):
        from coding_agent.wire.protocol import ApprovalRequest
        from coding_agent.ui import approval_prompt

        consumer, _, buf = self._make_consumer()

        async def mock_prompt(console, req):
            from coding_agent.wire.protocol import ApprovalResponse

            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=True,
                scope="once",
            )

        monkeypatch.setattr(approval_prompt, "prompt_approval", mock_prompt)

        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="bash",
            args={"command": "ls"},
        )
        resp = await consumer.request_approval(req)
        assert resp.approved is True
        assert resp.request_id == "r1"

    @pytest.mark.asyncio
    async def test_approval_rejection(self, monkeypatch):
        from coding_agent.wire.protocol import ApprovalRequest
        from coding_agent.ui import approval_prompt

        consumer, _, buf = self._make_consumer()

        async def mock_prompt(console, req):
            from coding_agent.wire.protocol import ApprovalResponse

            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=False,
                feedback="too dangerous",
            )

        monkeypatch.setattr(approval_prompt, "prompt_approval", mock_prompt)

        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="bash",
            args={"command": "rm -rf /"},
        )
        resp = await consumer.request_approval(req)
        assert resp.approved is False
        assert resp.feedback == "too dangerous"

    @pytest.mark.asyncio
    async def test_collapse_group_attribute_exists(self):
        consumer, _, _ = self._make_consumer()
        assert hasattr(consumer, "_collapse_group")
        assert consumer._collapse_group is None

    @pytest.mark.asyncio
    async def test_session_approve_skips_future_prompts(self, monkeypatch):
        from coding_agent.wire.protocol import ApprovalRequest
        from coding_agent.ui import approval_prompt

        consumer, _, buf = self._make_consumer()
        call_count = 0

        async def mock_prompt(console, req):
            nonlocal call_count
            call_count += 1
            from coding_agent.wire.protocol import ApprovalResponse

            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=True,
                scope="session",
            )

        monkeypatch.setattr(approval_prompt, "prompt_approval", mock_prompt)

        req1 = ApprovalRequest(
            session_id="s1", request_id="r1", tool="bash", args={"command": "ls"}
        )
        resp1 = await consumer.request_approval(req1)
        assert resp1.approved is True
        assert call_count == 1

        req2 = ApprovalRequest(
            session_id="s1", request_id="r2", tool="bash", args={"command": "pwd"}
        )
        resp2 = await consumer.request_approval(req2)
        assert resp2.approved is True
        assert call_count == 1


class TestCollapseGrouping:
    def _make_consumer(self) -> tuple[RichConsumer, StreamingRenderer, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        consumer = RichConsumer(renderer)
        return consumer, renderer, buf

    @pytest.mark.asyncio
    async def test_single_read_collapsed(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="some content")
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "some content" not in output

    @pytest.mark.asyncio
    async def test_consecutive_reads_collapsed(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="aaa")
        )
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "b.py"}, call_id="c2"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c2", tool_name="file_read", result="bbb")
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "Read 2 files" in output
        assert "aaa" not in output
        assert "bbb" not in output

    @pytest.mark.asyncio
    async def test_grep_search_collapsed(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="grep_search", arguments={"pattern": "TODO"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(
                call_id="c1", tool_name="grep_search", result="main.py:42: TODO"
            )
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "Searched for 1 pattern" in output
        assert "main.py:42" not in output

    @pytest.mark.asyncio
    async def test_non_collapsible_flushes_group(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="aaa")
        )
        await consumer.emit(
            ToolCallDelta(
                tool_name="bash_run", arguments={"command": "ls"}, call_id="c2"
            )
        )
        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "bash_run" in output

    @pytest.mark.asyncio
    async def test_stream_delta_flushes_group(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="x")
        )
        await consumer.emit(StreamDelta(content="Here is my analysis"))
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "Here is my analysis" in output

    @pytest.mark.asyncio
    async def test_bash_renders_full_panel(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="bash_run", arguments={"command": "ls"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="bash_run", result="file.py")
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "file.py" in output

    @pytest.mark.asyncio
    async def test_mixed_collapsed_then_bash(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="data")
        )
        await consumer.emit(
            ToolCallDelta(
                tool_name="bash_run", arguments={"command": "echo hi"}, call_id="c2"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c2", tool_name="bash_run", result="hi")
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "hi" in output

    @pytest.mark.asyncio
    async def test_realistic_multi_tool_sequence(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="glob_files", arguments={"pattern": "**/*.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="glob_files", result="a.py\nb.py")
        )
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c2"
            )
        )
        await consumer.emit(
            ToolResultDelta(
                call_id="c2", tool_name="file_read", result="def run(): pass"
            )
        )
        await consumer.emit(
            ToolCallDelta(
                tool_name="grep_search", arguments={"pattern": "TODO"}, call_id="c3"
            )
        )
        await consumer.emit(
            ToolResultDelta(
                call_id="c3", tool_name="grep_search", result="a.py:1: TODO"
            )
        )
        await consumer.emit(
            ToolCallDelta(
                tool_name="bash_run", arguments={"command": "pytest -q"}, call_id="c4"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c4", tool_name="bash_run", result="3 passed")
        )
        await consumer.emit(StreamDelta(content="I found the issue in a.py."))
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )

        output = buf.getvalue()
        lowered = output.lower()
        assert "searched for 1 pattern" in lowered
        assert "read 1 file" in lowered
        assert "listed 1 pattern" in lowered
        assert "3 passed" in output
        assert "I found the issue in a.py." in output
        assert "def run(): pass" not in output
        assert "a.py:1: TODO" not in output

    @pytest.mark.asyncio
    async def test_two_separate_groups(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="alpha")
        )
        await consumer.emit(StreamDelta(content="First group complete."))
        await consumer.emit(
            ToolCallDelta(
                tool_name="grep_search", arguments={"pattern": "TODO"}, call_id="c2"
            )
        )
        await consumer.emit(
            ToolResultDelta(
                call_id="c2", tool_name="grep_search", result="b.py:5: TODO"
            )
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )

        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "First group complete." in output
        assert "Searched for 1 pattern" in output
        assert output.index("Read 1 file") < output.index("First group complete.")
        assert output.index("First group complete.") < output.index(
            "Searched for 1 pattern"
        )

    @pytest.mark.asyncio
    async def test_collapse_group_cleared_after_turnend(self):
        consumer, _, _ = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="x")
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        assert consumer._collapse_group is None

    @pytest.mark.asyncio
    async def test_error_in_collapsed_group_shows_warning(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolResultDelta(
                call_id="c1", tool_name="file_read", result="err", is_error=True
            )
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        output = buf.getvalue()
        assert "\u26a0" in output

    @pytest.mark.asyncio
    async def test_non_collapsible_call_waits_for_pending_collapsible_result(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(
            ToolCallDelta(
                tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"
            )
        )
        await consumer.emit(
            ToolCallDelta(
                tool_name="bash_run", arguments={"command": "ls"}, call_id="c2"
            )
        )
        interim_output = buf.getvalue()
        assert "Read 1 file" not in interim_output
        assert "bash_run" in interim_output

        await consumer.emit(
            ToolResultDelta(call_id="c1", tool_name="file_read", result="content")
        )
        await consumer.emit(
            ToolResultDelta(call_id="c2", tool_name="bash_run", result="file.py")
        )
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )

        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "content" not in output


class TestThinkingAndStatusHandling:
    def _make_consumer(self) -> tuple[RichConsumer, StreamingRenderer, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        consumer = RichConsumer(renderer)
        return consumer, renderer, buf

    # ── ThinkingDelta: basic thinking state ──

    @pytest.mark.asyncio
    async def test_thinking_delta_calls_thinking_start(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="reasoning..."))
        renderer.thinking_start.assert_called_once()
        renderer.thinking.assert_called_once_with("reasoning...")
        assert consumer._phase == "thinking"

    @pytest.mark.asyncio
    async def test_subsequent_thinking_delta_does_not_re_call_start(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_update = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="first"))
        await consumer.emit(ThinkingDelta(text="second"))

        renderer.thinking_start.assert_called_once()

    # ── Phase transition: thinking → streaming ──

    @pytest.mark.asyncio
    async def test_stream_delta_after_thinking_calls_thinking_end(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="reasoning"))
        assert consumer._phase == "thinking"

        await consumer.emit(StreamDelta(content="Answer text"))
        renderer.thinking_end.assert_called_once()
        assert consumer._phase != "thinking"
        assert consumer._stream_active is True

    # ── TurnStatusDelta: token tracking ──

    @pytest.mark.asyncio
    async def test_turn_status_delta_stores_tokens(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.update_status = MagicMock()

        await consumer.emit(TurnStatusDelta(phase="idle", tokens_in=150, tokens_out=75))

        assert consumer._turn_tokens_in == 150
        assert consumer._turn_tokens_out == 75

    @pytest.mark.asyncio
    async def test_turn_status_delta_updates_internal_status_only(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.update_status = MagicMock()

        await consumer.emit(
            TurnStatusDelta(phase="idle", tokens_in=200, tokens_out=100)
        )

        renderer.update_status.assert_not_called()
        assert consumer._turn_tokens_in == 200
        assert consumer._turn_tokens_out == 100

    @pytest.mark.asyncio
    async def test_turn_status_delta_does_not_write_renderer_status_line(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.update_status = MagicMock()

        await consumer.emit(
            TurnStatusDelta(phase="idle", tokens_in=200, tokens_out=100)
        )

        renderer.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_status_delta_during_thinking_does_not_end_thinking(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_end = MagicMock()
        renderer.thinking = MagicMock()
        renderer.update_status = MagicMock()

        await consumer.emit(ThinkingDelta(text="reasoning"))
        await consumer.emit(
            TurnStatusDelta(phase="thinking", tokens_in=10, tokens_out=5)
        )

        renderer.thinking_end.assert_not_called()
        assert consumer._phase == "thinking"

    @pytest.mark.asyncio
    async def test_tool_call_after_thinking_ends_thinking_before_rendering_tool(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_end = MagicMock()
        renderer.tool_call = MagicMock()

        await consumer.emit(ThinkingDelta(text="reasoning"))
        await consumer.emit(
            ToolCallDelta(
                tool_name="custom_tool", arguments={"path": "a.txt"}, call_id="c1"
            )
        )

        renderer.thinking_end.assert_called_once()
        renderer.tool_call.assert_called_once()

    # ── TurnEnd: state reset ──

    @pytest.mark.asyncio
    async def test_turn_end_resets_turn_state(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_end = MagicMock()
        renderer.update_status = MagicMock()

        await consumer.emit(ThinkingDelta(text="thinking"))
        await consumer.emit(StreamDelta(content="response"))
        await consumer.emit(TurnStatusDelta(phase="idle", tokens_in=50, tokens_out=25))
        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )

        assert consumer._phase == "idle"
        assert consumer._turn_start is None
        assert consumer._turn_tokens_in == 0
        assert consumer._turn_tokens_out == 0

    @pytest.mark.asyncio
    async def test_turn_end_during_thinking_calls_thinking_end(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="still thinking"))
        assert consumer._phase == "thinking"

        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        renderer.thinking_end.assert_called_once()
        assert consumer._phase == "idle"

    # ── Turn timing ──

    @pytest.mark.asyncio
    async def test_first_event_starts_turn_timer(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()

        assert consumer._turn_start is None
        await consumer.emit(ThinkingDelta(text="think"))
        assert consumer._turn_start is not None
        assert isinstance(consumer._turn_start, float)

    @pytest.mark.asyncio
    async def test_turn_timer_starts_on_stream_delta_too(self):
        consumer, renderer, _ = self._make_consumer()
        assert consumer._turn_start is None
        await consumer.emit(StreamDelta(content="direct answer"))
        assert consumer._turn_start is not None

    # ── Heartbeat ──

    @pytest.mark.asyncio
    async def test_thinking_heartbeat_starts_during_thinking(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_update = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="reasoning"))
        assert consumer._thinking_heartbeat_task is not None
        assert not consumer._thinking_heartbeat_task.done()

        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )

    @pytest.mark.asyncio
    async def test_heartbeat_calls_thinking_update_periodically(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_update = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="thinking hard"))

        await asyncio.sleep(0.3)

        assert renderer.thinking_update.call_count >= 1
        args = renderer.thinking_update.call_args
        elapsed = args[1].get("elapsed_seconds", args[0][0] if args[0] else None)
        assert elapsed is not None
        assert elapsed > 0

        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )

    @pytest.mark.asyncio
    async def test_heartbeat_stopped_on_stream_transition(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_update = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="think"))
        assert consumer._thinking_heartbeat_task is not None

        await consumer.emit(StreamDelta(content="answer"))
        assert (
            consumer._thinking_heartbeat_task is None
            or consumer._thinking_heartbeat_task.done()
        )

        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )

    @pytest.mark.asyncio
    async def test_heartbeat_stopped_on_turn_end(self):
        consumer, renderer, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_update = MagicMock()
        renderer.thinking_end = MagicMock()

        await consumer.emit(ThinkingDelta(text="think"))
        assert consumer._thinking_heartbeat_task is not None

        await consumer.emit(
            TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED)
        )
        assert (
            consumer._thinking_heartbeat_task is None
            or consumer._thinking_heartbeat_task.done()
        )


class TestThinkingControlsAndStatusCallbacks:
    def _make_consumer(
        self,
        *,
        thinking_enabled=lambda: True,
        thinking_effort=lambda: "medium",
    ) -> tuple[RichConsumer, StreamingRenderer, StringIO, list[dict[str, object]]]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        calls: list[dict[str, object]] = []
        consumer = RichConsumer(
            renderer,
            thinking_enabled=thinking_enabled,
            thinking_effort=thinking_effort,
            on_status=lambda snapshot: calls.append(snapshot),
        )
        return consumer, renderer, buf, calls

    @pytest.mark.asyncio
    async def test_thinking_delta_is_ignored_when_thinking_disabled(self):
        consumer, renderer, _, _ = self._make_consumer(thinking_enabled=lambda: False)
        renderer.thinking_start = MagicMock()

        await consumer.emit(ThinkingDelta(text="hidden"))

        renderer.thinking_start.assert_not_called()
        assert consumer._phase == "idle"

    @pytest.mark.asyncio
    async def test_turn_status_delta_ends_thinking_without_renderer_status_line(self):
        consumer, renderer, _, _ = self._make_consumer()
        renderer.thinking_start = MagicMock()
        renderer.thinking_end = MagicMock()
        renderer.update_status = MagicMock()

        await consumer.emit(ThinkingDelta(text="reasoning"))
        await consumer.emit(TurnStatusDelta(phase="idle", tokens_in=10, tokens_out=5))

        renderer.thinking_end.assert_called_once()
        renderer.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_callback_receives_token_snapshot(self):
        consumer, renderer, _, calls = self._make_consumer()
        renderer.update_status = MagicMock()

        await consumer.emit(
            TurnStatusDelta(
                phase="idle",
                tokens_in=321,
                tokens_out=123,
                model_name="gpt-4o",
                context_percent=12.5,
            )
        )

        assert calls
        assert calls[-1]["tokens_in"] == 321
        assert calls[-1]["tokens_out"] == 123
        assert calls[-1]["model_name"] == "gpt-4o"
        assert calls[-1]["context_percent"] == 12.5

    def test_thinking_effort_changes_heartbeat_interval(self):
        low_consumer, _, _, _ = self._make_consumer(thinking_effort=lambda: "low")
        medium_consumer, _, _, _ = self._make_consumer(thinking_effort=lambda: "medium")
        high_consumer, _, _, _ = self._make_consumer(thinking_effort=lambda: "high")

        assert (
            low_consumer._thinking_heartbeat_interval()
            > medium_consumer._thinking_heartbeat_interval()
        )
        assert (
            medium_consumer._thinking_heartbeat_interval()
            > high_consumer._thinking_heartbeat_interval()
        )
