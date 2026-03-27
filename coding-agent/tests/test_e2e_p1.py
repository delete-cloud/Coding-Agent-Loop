"""E2E integration test for P1: planner + subagent with mock provider."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.core.planner import PlanManager
from coding_agent.core.tape import Entry, Tape
from coding_agent.providers.base import StreamEvent, ToolCall, ToolSchema
from coding_agent.tools.planner import register_planner_tools
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
        return ApprovalResponse(call_id=req.call_id, decision="approve")


class MockProvider:
    """Provider with scripted responses for E2E test."""

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


class TestE2EP1:
    @pytest.mark.asyncio
    async def test_agent_creates_plan_then_completes(self):
        """Agent uses todo_write to create a plan, then responds."""
        # Step 1: LLM calls todo_write to create a plan
        # Step 2: LLM responds with final text
        provider = MockProvider([
            # Step 1: Call todo_write
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="call_1",
                        name="todo_write",
                        arguments={
                            "tasks": [
                                {"title": "Read the code", "status": "in_progress"},
                                {"title": "Fix the bug", "status": "todo"},
                            ]
                        },
                    ),
                ),
                StreamEvent(type="done"),
            ],
            # Step 2: Final response
            [
                StreamEvent(type="delta", text="Plan created. Starting work."),
                StreamEvent(type="done"),
            ],
        ])

        planner = PlanManager()
        registry = ToolRegistry()
        register_planner_tools(registry, planner)

        tape = Tape(path=None)
        consumer = MockConsumer()
        context = Context(128000, "You are a coding agent.", planner=planner)

        loop = AgentLoop(
            provider=provider,
            tools=registry,
            tape=tape,
            context=context,
            consumer=consumer,
            max_steps=10,
        )

        result = await loop.run_turn("Fix the bug in main.py")

        assert result.stop_reason == "no_tool_calls"
        assert "Plan created" in result.final_message

        # Verify plan was created
        assert len(planner.tasks) == 2
        assert planner.tasks[0].title == "Read the code"

        # Verify plan is in context
        messages = await context.build_working_set(tape)
        plan_msgs = [m for m in messages if m.get("role") == "system" and "Current Plan" in m.get("content", "")]
        assert len(plan_msgs) == 1

    @pytest.mark.asyncio
    async def test_agent_dispatches_subagent(self):
        """Agent dispatches a sub-agent via the subagent tool."""
        # Main agent calls subagent tool
        # Sub-agent provider returns a simple response

        class SequencedProvider:
            """Provider that serves both main agent and sub-agent."""

            def __init__(self):
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
                self._call_index += 1
                if self._call_index == 1:
                    # Main agent: dispatch subagent
                    yield StreamEvent(
                        type="tool_call",
                        tool_call=ToolCall(
                            id="call_1",
                            name="subagent",
                            arguments={"goal": "Read the README file"},
                        ),
                    )
                    yield StreamEvent(type="done")
                elif self._call_index == 2:
                    # Sub-agent: respond
                    yield StreamEvent(type="delta", text="README contains project docs")
                    yield StreamEvent(type="done")
                else:
                    # Main agent: final response
                    yield StreamEvent(type="delta", text="Sub-agent found the README info.")
                    yield StreamEvent(type="done")

        provider = SequencedProvider()
        tape = Tape(path=None)
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        context = Context(128000, "You are a coding agent.")
        loop = AgentLoop(
            provider=provider,
            tools=registry,
            tape=tape,
            context=context,
            consumer=consumer,
            max_steps=10,
        )

        result = await loop.run_turn("What's in the README?")

        assert result.stop_reason == "no_tool_calls"
        assert "Sub-agent" in result.final_message or "README" in result.final_message

        # Verify tape has subagent entries (merged from fork)
        entries = tape.entries()
        kinds = [e.kind for e in entries]
        assert "anchor" in kinds  # subagent_start anchor
        assert "tool_call" in kinds
        assert "tool_result" in kinds
