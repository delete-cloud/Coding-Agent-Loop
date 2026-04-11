"""Tests for ToolResultDelta wire message and adapter handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Any
from pydantic import BaseModel

from agentkit.providers.models import ToolResultEvent
from agentkit.runtime.pipeline import PipelineContext
from agentkit.tape.tape import Tape
from coding_agent.adapter import PipelineAdapter
from coding_agent.wire.protocol import ToolResultDelta


class TestToolResultDelta:
    def test_creation(self):
        msg = ToolResultDelta(
            call_id="call_123",
            tool_name="bash",
            result="output",
        )
        assert msg.call_id == "call_123"
        assert msg.tool_name == "bash"
        assert msg.result == "output"
        assert msg.is_error is False

    def test_error_result(self):
        msg = ToolResultDelta(
            call_id="call_err",
            tool_name="bash",
            result="Error: fail",
            is_error=True,
        )
        assert msg.is_error is True

    def test_inherits_wire_message(self):
        from coding_agent.wire.protocol import WireMessage

        msg = ToolResultDelta(call_id="c1", tool_name="t", result="r")
        assert isinstance(msg, WireMessage)


class TestPipelineAdapterToolResultHandling:
    @pytest.mark.asyncio
    async def test_tool_result_event_with_dict_is_stringified_for_wire_consumer(self):
        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call_123",
                name="bash_run",
                result={"stdout": "ok", "exit_code": 0},
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == {"stdout": "ok", "exit_code": 0}
        assert emitted.display_result == '{"stdout": "ok", "exit_code": 0}'

    @pytest.mark.asyncio
    async def test_tool_result_event_with_pydantic_model_serializes_for_wire_consumer(
        self,
    ):
        class OutputModel(BaseModel):
            value: int

        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call_456",
                name="structured_tool",
                result=OutputModel(value=7).model_dump(),
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == {"value": 7}
        assert emitted.display_result == '{"value": 7}'
