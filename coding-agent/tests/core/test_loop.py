"""Tests for AgentLoop with mock provider."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Any, AsyncIterator

from coding_agent.core.loop import AgentLoop, TurnOutcome
from coding_agent.core.context import Context
from coding_agent.core.tape import Tape
from coding_agent.providers.base import (
    ChatProvider,
    StreamEvent,
    ToolCall,
    ToolSchema,
)
from coding_agent.tools.registry import ToolRegistry
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireMessage,
)


class MockConsumer:
    """Mock wire consumer for testing."""

    def __init__(self, auto_approve: bool = True):
        self.auto_approve = auto_approve
        self.messages: list[WireMessage] = []
        self.approvals_requested: list[ApprovalRequest] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        self.approvals_requested.append(req)
        if self.auto_approve:
            return ApprovalResponse(call_id=req.call_id, decision="approve")
        return ApprovalResponse(
            call_id=req.call_id,
            decision="deny",
            feedback="Denied by mock consumer",
        )


class MockProvider:
    """Mock LLM provider for deterministic testing."""

    def __init__(self, responses: list[list[StreamEvent]]):
        """Initialize with list of response sequences.
        
        Each response sequence represents one step's events.
        """
        self._responses = responses
        self._call_index = 0
        self._max_context_size = 128000

    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def max_context_size(self) -> int:
        return self._max_context_size

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Return the next scripted response."""
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            # Default: return done
            yield StreamEvent(type="done")


class TestAgentLoopBasics:
    """Basic AgentLoop tests."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def tape(self, temp_dir: Path) -> Tape:
        return Tape.create(temp_dir)

    @pytest.fixture
    def context(self) -> Context:
        return Context(
            max_tokens=1000,
            system_prompt="You are a test agent.",
        )

    @pytest.fixture
    def tools(self) -> ToolRegistry:
        registry = ToolRegistry()
        
        async def mock_tool(value: str = "default") -> str:
            return json.dumps({"result": f"mock_result_{value}"})
        
        registry.register(
            name="mock_tool",
            description="A mock tool for testing",
            parameters={
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
            },
            handler=mock_tool,
        )
        return registry

    async def test_simple_text_response(self, tape: Tape, context: Context, tools: ToolRegistry):
        """Test simple text response with no tool calls."""
        # Mock provider returns text response
        provider = MockProvider([
            [StreamEvent(type="delta", text="Hello, "),
             StreamEvent(type="delta", text="world!"),
             StreamEvent(type="done")]
        ])
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        result = await loop.run_turn("Say hello")

        assert result.stop_reason == "no_tool_calls"
        assert result.final_message == "Hello, world!"
        assert result.steps_taken == 1

        # Check tape has user message and assistant message
        entries = tape.entries()
        assert len(entries) == 2
        assert entries[0].kind == "message"
        assert entries[0].payload["role"] == "user"
        assert entries[0].payload["content"] == "Say hello"
        assert entries[1].kind == "message"
        assert entries[1].payload["role"] == "assistant"
        assert entries[1].payload["content"] == "Hello, world!"

        # Check wire messages
        assert any(isinstance(m, TurnBegin) for m in consumer.messages)
        assert any(isinstance(m, TurnEnd) for m in consumer.messages)
        assert any(isinstance(m, StreamDelta) for m in consumer.messages)

    async def test_single_tool_call(self, tape: Tape, context: Context, tools: ToolRegistry):
        """Test single tool call workflow."""
        # Mock provider: first returns tool call, then text response
        provider = MockProvider([
            # Step 1: tool call
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="call_1", name="mock_tool", arguments={"value": "test"})
            ), StreamEvent(type="done")],
            # Step 2: text response
            [StreamEvent(type="delta", text="Done!"), StreamEvent(type="done")],
        ])
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        result = await loop.run_turn("Use the tool")

        assert result.stop_reason == "no_tool_calls"
        assert result.final_message == "Done!"
        assert result.steps_taken == 2

        # Check tape entries
        entries = tape.entries()
        assert len(entries) == 4  # user, tool_call, tool_result, assistant
        assert entries[0].kind == "message"  # user
        assert entries[1].kind == "tool_call"
        assert entries[1].payload["call_id"] == "call_1"
        assert entries[1].payload["tool"] == "mock_tool"
        assert entries[2].kind == "tool_result"
        assert "mock_result_test" in entries[2].payload["result"]
        assert entries[3].kind == "message"  # assistant final response

        # Check wire messages for tool call
        tool_begin_msgs = [m for m in consumer.messages if isinstance(m, ToolCallBegin)]
        tool_end_msgs = [m for m in consumer.messages if isinstance(m, ToolCallEnd)]
        assert len(tool_begin_msgs) == 1
        assert len(tool_end_msgs) == 1
        assert tool_begin_msgs[0].call_id == "call_1"
        assert tool_begin_msgs[0].tool == "mock_tool"

    async def test_multiple_tool_calls(self, tape: Tape, context: Context, tools: ToolRegistry):
        """Test multiple tool calls in one response."""
        # Add another mock tool
        async def mock_tool_2(data: str = "") -> str:
            return json.dumps({"result": f"tool2_{data}"})
        
        tools.register(
            name="mock_tool_2",
            description="Another mock tool",
            parameters={
                "type": "object",
                "properties": {
                    "data": {"type": "string"},
                },
            },
            handler=mock_tool_2,
        )

        provider = MockProvider([
            # Step 1: multiple tool calls
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(id="call_1", name="mock_tool", arguments={"value": "a"})
                ),
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(id="call_2", name="mock_tool_2", arguments={"data": "b"})
                ),
                StreamEvent(type="done"),
            ],
            # Step 2: text response
            [StreamEvent(type="delta", text="Completed both"), StreamEvent(type="done")],
        ])
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        result = await loop.run_turn("Use both tools")

        assert result.stop_reason == "no_tool_calls"
        assert result.steps_taken == 2

        # Check tape has both tool calls and results
        entries = tape.entries()
        tool_calls = [e for e in entries if e.kind == "tool_call"]
        tool_results = [e for e in entries if e.kind == "tool_result"]
        assert len(tool_calls) == 2
        assert len(tool_results) == 2

        # Check wire messages
        tool_begin_msgs = [m for m in consumer.messages if isinstance(m, ToolCallBegin)]
        assert len(tool_begin_msgs) == 2

    async def test_max_steps_reached(self, tape: Tape, context: Context, tools: ToolRegistry):
        """Test max steps limit."""
        # Provider always returns tool calls with different args to avoid doom loop
        provider = MockProvider([
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id=f"call_{i}", name="mock_tool", arguments={"value": f"step_{i}"})
            ), StreamEvent(type="done")]
            for i in range(5)  # 5 tool call responses
        ])
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
            max_steps=3,
        )

        result = await loop.run_turn("Keep calling tools")

        assert result.stop_reason == "max_steps_reached"
        assert result.steps_taken == 3

    async def test_doom_loop_detection(self, tape: Tape, context: Context, tools: ToolRegistry):
        """Test doom loop detection aborts loop."""
        # Provider keeps returning the same tool call
        provider = MockProvider([
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id=f"call_{i}", name="mock_tool", arguments={"value": "same"})
            ), StreamEvent(type="done")]
            for i in range(5)
        ])
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
            doom_threshold=3,
            max_steps=10,
        )

        result = await loop.run_turn("Repeating tool calls")

        assert result.stop_reason == "doom_loop"
        assert "Repetitive tool call" in result.final_message

    async def test_approval_denied(self, tape: Tape, context: Context, tools: ToolRegistry):
        """Test approval denial handling."""
        provider = MockProvider([
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="call_1", name="mock_tool", arguments={})
            ), StreamEvent(type="done")],
            [StreamEvent(type="delta", text="Next"), StreamEvent(type="done")],
        ])
        consumer = MockConsumer(auto_approve=False)

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        result = await loop.run_turn("Try to use tool")

        # Tool call was denied but turn completed
        assert result.stop_reason == "no_tool_calls"
        
        # Check tool result shows denial
        entries = tape.entries()
        tool_results = [e for e in entries if e.kind == "tool_result"]
        assert len(tool_results) == 1
        assert "DENIED" in tool_results[0].payload["result"]

    async def test_different_tool_calls_reset_doom(self, tape: Tape, context: Context, tools: ToolRegistry):
        """Test that different tool calls reset doom counter."""
        # Add a second tool
        async def mock_tool_2(data: str = "") -> str:
            return json.dumps({"result": "ok"})
        
        tools.register(
            name="mock_tool_2",
            description="Another mock tool",
            parameters={"type": "object", "properties": {"data": {"type": "string"}}},
            handler=mock_tool_2,
        )

        # Alternating tool calls should not trigger doom
        provider = MockProvider([
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="call_1", name="mock_tool", arguments={"value": "x"})
            ), StreamEvent(type="done")],
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="call_2", name="mock_tool_2", arguments={"data": "y"})
            ), StreamEvent(type="done")],
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="call_3", name="mock_tool", arguments={"value": "x"})
            ), StreamEvent(type="done")],
            [StreamEvent(type="delta", text="Done"), StreamEvent(type="done")],
        ])
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
            doom_threshold=2,
        )

        result = await loop.run_turn("Alternating tools")

        # Should complete without doom loop
        assert result.stop_reason == "no_tool_calls"


class TestAgentLoopEdgeCases:
    """Edge case tests for AgentLoop."""

    @pytest.fixture
    def temp_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def tape(self, temp_dir: Path) -> Tape:
        return Tape.create(temp_dir)

    @pytest.fixture
    def context(self) -> Context:
        return Context(max_tokens=1000, system_prompt="Test agent.")

    async def test_empty_tool_calls_followed_by_text(self, tape: Tape, context: Context):
        """Test handling when provider returns done without tool calls or text."""
        provider = MockProvider([
            [StreamEvent(type="done")],  # No content
        ])
        tools = ToolRegistry()
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        result = await loop.run_turn("Empty response")

        assert result.stop_reason == "no_tool_calls"
        assert result.final_message == ""

    async def test_multiple_approval_requests(self, tape: Tape, context: Context):
        """Test approval is requested for each tool call."""
        provider = MockProvider([
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(id="call_1", name="tool1", arguments={})
                ),
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(id="call_2", name="tool1", arguments={})
                ),
                StreamEvent(type="done"),
            ],
            [StreamEvent(type="delta", text="Done"), StreamEvent(type="done")],
        ])
        
        tools = ToolRegistry()
        tools.register(
            name="tool1",
            description="Test tool",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "ok",
        )
        consumer = MockConsumer(auto_approve=True)

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        await loop.run_turn("Multiple tools")

        # Should have requested approval for each tool call
        assert len(consumer.approvals_requested) == 2
        assert consumer.approvals_requested[0].call_id == "call_1"
        assert consumer.approvals_requested[1].call_id == "call_2"

    async def test_turn_begin_emitted(self, tape: Tape, context: Context):
        """Test that TurnBegin is emitted at start."""
        provider = MockProvider([[StreamEvent(type="done")]])
        tools = ToolRegistry()
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        await loop.run_turn("Test")

        # First message should be TurnBegin
        assert len(consumer.messages) > 0
        assert isinstance(consumer.messages[0], TurnBegin)

    async def test_tool_result_recorded_in_tape(self, tape: Tape, context: Context):
        """Test that tool results are properly recorded."""
        async def test_tool(input: str) -> str:
            return json.dumps({"processed": input.upper()})

        tools = ToolRegistry()
        tools.register(
            name="test_tool",
            description="Test tool",
            parameters={
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            },
            handler=test_tool,
        )

        provider = MockProvider([
            [StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="tc_1", name="test_tool", arguments={"input": "hello"})
            ), StreamEvent(type="done")],
            [StreamEvent(type="delta", text="OK"), StreamEvent(type="done")],
        ])
        consumer = MockConsumer()

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            tape=tape,
            context=context,
            consumer=consumer,
        )

        await loop.run_turn("Test tool result")

        # Find tool result in tape
        entries = tape.entries()
        tool_results = [e for e in entries if e.kind == "tool_result"]
        assert len(tool_results) == 1
        
        result_data = json.loads(tool_results[0].payload["result"])
        assert result_data["processed"] == "HELLO"
