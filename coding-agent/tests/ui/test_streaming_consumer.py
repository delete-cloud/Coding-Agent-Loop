import pytest
from io import StringIO
from rich.console import Console

from coding_agent.ui.stream_renderer import StreamingRenderer
from coding_agent.ui.rich_consumer import RichConsumer
from coding_agent.wire.protocol import (
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
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
