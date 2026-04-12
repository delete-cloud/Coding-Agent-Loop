"""Tests for ToolResultDelta wire message and adapter handling."""

from collections import UserDict
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
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

    @pytest.mark.asyncio
    async def test_tool_result_event_with_pydantic_model_instance_serializes_for_wire_consumer(
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
                tool_call_id="call_789",
                name="structured_tool",
                result=cast(Any, OutputModel(value=9)),
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == {"value": 9}
        assert emitted.display_result == '{"value": 9}'

    @pytest.mark.asyncio
    async def test_tool_result_event_redacts_sensitive_fields_in_display_result(self):
        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        payload = {"stdout": "ok", "password": "supersecret"}

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call-123",
                name="bash_run",
                result=payload,
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == payload
        assert '"stdout": "ok"' in emitted.display_result
        assert '"password": "***"' in emitted.display_result
        assert "supersecret" not in emitted.display_result

    @pytest.mark.asyncio
    async def test_tool_result_event_redacts_credentials_in_url_display_result(self):
        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call-456",
                name="bash_run",
                result="redis://:supersecret@example:6379/0",
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == "redis://:supersecret@example:6379/0"
        assert emitted.display_result == "redis://example:6379/0"

    @pytest.mark.asyncio
    async def test_tool_result_event_redacts_credentials_in_unix_socket_url(self):
        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call-unix",
                name="bash_run",
                result="redis://:supersecret@/tmp/redis.sock",
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == "redis://:supersecret@/tmp/redis.sock"
        assert emitted.display_result == "redis:/tmp/redis.sock"

    @pytest.mark.asyncio
    async def test_tool_result_event_redacts_credentials_in_ipv6_url(self):
        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call-ipv6",
                name="bash_run",
                result="redis://:supersecret@[2001:db8::1]:6379/0",
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == "redis://:supersecret@[2001:db8::1]:6379/0"
        assert emitted.display_result == "redis://[2001:db8::1]:6379/0"

    @pytest.mark.asyncio
    async def test_tool_result_event_redacts_secret_values_in_freeform_display_text(
        self,
    ):
        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call-789",
                name="bash_run",
                result="AUTHORIZATION: Bearer supersecret-token password=hunter2 api_key: abc123",
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.result == (
            "AUTHORIZATION: Bearer supersecret-token password=hunter2 api_key: abc123"
        )
        assert "supersecret-token" not in emitted.display_result
        assert "hunter2" not in emitted.display_result
        assert "abc123" not in emitted.display_result
        assert "AUTHORIZATION: Bearer ***" in emitted.display_result
        assert "password=***" in emitted.display_result
        assert "api_key: ***" in emitted.display_result

    @pytest.mark.asyncio
    async def test_tool_result_event_formats_dict_subclasses_as_json(self):
        consumer = AsyncMock()
        pipeline = MagicMock()
        ctx = PipelineContext(tape=Tape(), session_id="session-1")
        adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

        payload = UserDict({"stdout": "ok", "token": "secret"})

        await adapter._handle_event(
            ToolResultEvent(
                tool_call_id="call-userdict",
                name="bash_run",
                result=cast(Any, payload),
            )
        )

        emitted = consumer.emit.await_args.args[0]
        assert isinstance(emitted, ToolResultDelta)
        assert emitted.display_result.startswith("{")
        assert '"stdout": "ok"' in emitted.display_result
        assert '"token": "***"' in emitted.display_result
