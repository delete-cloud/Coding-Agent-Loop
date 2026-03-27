"""Tests for subagent tool registration."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from coding_agent.core.tape import Entry, Tape
from coding_agent.providers.base import StreamEvent, ToolSchema
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.subagent import register_subagent_tool
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)


class MockConsumer:
    def __init__(self):
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(request_id=req.request_id, approved=True)


class MockProvider:
    def __init__(self, responses: list[list[StreamEvent]]):
        self._responses = responses
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "mock"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            yield StreamEvent(type="delta", text="fallback")
            yield StreamEvent(type="done")


class TestSubagentTool:
    @pytest.mark.asyncio
    async def test_tool_registered(self):
        provider = MockProvider([])
        tape = Tape()
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )
        assert "subagent" in registry.list_tools()

    @pytest.mark.asyncio
    async def test_tool_dispatches_subagent(self):
        provider = MockProvider([
            [StreamEvent(type="delta", text="Sub-task done"), StreamEvent(type="done")],
        ])
        tape = Tape()
        tape.append(Entry.message("user", "main goal"))
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        result = await registry.execute("subagent", {"goal": "Read the README"})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert "Sub-task done" in parsed["output"]

    @pytest.mark.asyncio
    async def test_tool_returns_json_result(self):
        provider = MockProvider([
            [StreamEvent(type="delta", text="Result here"), StreamEvent(type="done")],
        ])
        tape = Tape()
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        result = await registry.execute("subagent", {"goal": "Do something"})
        parsed = json.dumps(result)
        assert "success" in result
        assert "output" in result
        assert "stop_reason" in result
