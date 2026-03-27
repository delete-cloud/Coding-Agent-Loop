"""Tests for SubAgent dispatch."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from coding_agent.agents.subagent import SubAgent, SubAgentResult
from coding_agent.core.tape import Tape, Entry
from coding_agent.providers.base import StreamEvent, ToolCall, ToolSchema
from coding_agent.tools.registry import ToolRegistry
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
    """Provider that returns scripted responses."""

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
            yield StreamEvent(type="delta", text="No more responses")
            yield StreamEvent(type="done")


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_successful_subagent_run(self):
        """Sub-agent runs, succeeds, result is returned."""
        provider = MockProvider([
            [StreamEvent(type="delta", text="Done with sub-task"), StreamEvent(type="done")],
        ])
        tools = ToolRegistry()
        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        consumer = MockConsumer()

        subagent = SubAgent(
            provider=provider,
            consumer=consumer,
            max_steps=10,
        )
        result = await subagent.run(
            goal="Sub-task: read a file",
            parent_tape=parent_tape,
            tools=tools,
        )

        assert isinstance(result, SubAgentResult)
        assert result.success is True
        assert result.output == "Done with sub-task"

    @pytest.mark.asyncio
    async def test_subagent_forks_tape(self):
        """Sub-agent works on a forked tape, parent tape is unmodified during execution."""
        provider = MockProvider([
            [StreamEvent(type="delta", text="Sub result"), StreamEvent(type="done")],
        ])
        tools = ToolRegistry()
        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        original_count = len(parent_tape.entries())
        consumer = MockConsumer()

        subagent = SubAgent(provider=provider, consumer=consumer, max_steps=10)
        result = await subagent.run(
            goal="Sub-task",
            parent_tape=parent_tape,
            tools=tools,
        )

        # Sub-agent succeeded → parent tape should have merged entries
        assert result.success is True
        assert len(parent_tape.entries()) > original_count

    @pytest.mark.asyncio
    async def test_subagent_failure_does_not_merge(self):
        """Sub-agent that hits max_steps does not merge into parent tape."""
        # Provider returns different tool calls each time to avoid doom loop detection
        # Each iteration uses a different tool call with unique arguments
        responses = [
            [StreamEvent(type="tool_call", tool_call=ToolCall(id=f"c{i}", name="echo", arguments={"text": f"loop{i}"})), StreamEvent(type="done")]
            for i in range(15)
        ]
        provider = MockProvider(responses)

        async def echo(text: str) -> str:
            return "echoed"

        tools = ToolRegistry()
        tools.register(
            name="echo",
            description="Echo text",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=echo,
        )

        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        original_entries = [e.to_dict() for e in parent_tape.entries()]
        consumer = MockConsumer()

        subagent = SubAgent(provider=provider, consumer=consumer, max_steps=3)
        result = await subagent.run(
            goal="This will fail",
            parent_tape=parent_tape,
            tools=tools,
        )

        assert result.success is False
        assert result.stop_reason == "max_steps_reached"
        # Parent tape should be unchanged
        current_entries = [e.to_dict() for e in parent_tape.entries()]
        assert len(current_entries) == len(original_entries)

    @pytest.mark.asyncio
    async def test_subagent_depth_limit(self):
        """Sub-agent respects max_depth."""
        provider = MockProvider([])
        tools = ToolRegistry()
        parent_tape = Tape()
        consumer = MockConsumer()

        subagent = SubAgent(
            provider=provider,
            consumer=consumer,
            max_steps=10,
            max_depth=2,
        )
        # depth > max_depth should fail (depth=3 exceeds max_depth=2)
        result = await subagent.run(
            goal="Too deep",
            parent_tape=parent_tape,
            tools=tools,
            depth=3,
        )

        assert result.success is False
        assert "depth" in result.output.lower()

    @pytest.mark.asyncio
    async def test_subagent_handoff_anchor_created(self):
        """Sub-agent creates a handoff anchor on the forked tape."""
        provider = MockProvider([
            [StreamEvent(type="delta", text="Done"), StreamEvent(type="done")],
        ])
        tools = ToolRegistry()
        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        consumer = MockConsumer()

        subagent = SubAgent(provider=provider, consumer=consumer, max_steps=10)
        result = await subagent.run(
            goal="Sub-task goal",
            parent_tape=parent_tape,
            tools=tools,
        )

        assert result.success is True
        # After merge, parent tape should contain the subagent anchor
        anchors = [e for e in parent_tape.entries() if e.kind == "anchor"]
        assert len(anchors) >= 1
        assert anchors[0].payload["name"] == "subagent_start"
