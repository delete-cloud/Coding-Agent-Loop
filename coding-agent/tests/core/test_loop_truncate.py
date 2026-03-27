"""Tests for AgentLoop result truncation."""

import json
import pytest

from coding_agent.core.loop import AgentLoop, TurnOutcome
from coding_agent.core.context import Context
from coding_agent.core.tape import Tape
from coding_agent.tools.registry import ToolRegistry
from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.wire import ApprovalResponse


class MockProvider:
    """Mock provider for testing."""
    
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
    
    async def stream(self, messages, tools=None):
        from coding_agent.providers.base import StreamEvent, ToolCall
        
        response = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        
        if isinstance(response, str):
            yield StreamEvent(type="delta", text=response)
            yield StreamEvent(type="done")
        elif isinstance(response, ToolCall):
            yield StreamEvent(type="tool_call", tool_call=response)
            yield StreamEvent(type="done")
    
    @property
    def model_name(self):
        return "mock"
    
    @property
    def max_context_size(self):
        return 128000


class TestToolResultTruncation:
    """Test that tool results are truncated when too large."""

    @pytest.fixture
    def setup(self, tmp_path):
        """Create AgentLoop with mock components."""
        tape = Tape(tmp_path / "test.jsonl")
        context = Context(max_tokens=4000, system_prompt="System")
        consumer = HeadlessConsumer()
        
        # Create registry with a tool that returns large output
        registry = ToolRegistry()
        
        async def large_output_tool():
            return "x" * 50000  # 50k chars
        
        registry.register(
            name="large_output",
            description="Returns large output",
            parameters={"type": "object", "properties": {}},
            handler=large_output_tool,
        )
        
        # Mock consumer to auto-approve
        async def mock_request_approval(req):
            return ApprovalResponse(approved=True)
        
        consumer.request_approval = mock_request_approval
        
        return {
            "tape": tape,
            "context": context,
            "consumer": consumer,
            "registry": registry,
        }

    @pytest.mark.asyncio
    async def test_large_tool_result_truncated(self, setup):
        """Test that large tool results are truncated."""
        from coding_agent.providers.base import ToolCall
        
        # Provider returns a tool call
        tool_call = ToolCall(
            id="call_1",
            name="large_output",
            arguments={},
        )
        # Then returns text (no more tool calls)
        provider = MockProvider([tool_call, "Done"])
        
        loop = AgentLoop(
            provider=provider,
            tools=setup["registry"],
            tape=setup["tape"],
            context=setup["context"],
            consumer=setup["consumer"],
            max_steps=5,
        )
        
        result = await loop.run_turn("Get large output")
        
        # Check result - should complete normally
        assert result.stop_reason in ["max_steps_reached", "no_tool_calls", "doom_loop"]
        
        # Check that tape contains truncated result
        entries = setup["tape"].entries()
        tool_results = [e for e in entries if e.kind == "tool_result"]
        assert len(tool_results) >= 1
        
        result_content = tool_results[0].payload["result"]
        # Should be truncated
        assert len(result_content) < 20000
        assert "truncated" in result_content

    @pytest.mark.asyncio
    async def test_small_tool_result_not_truncated(self, setup):
        """Test that small tool results are not truncated."""
        from coding_agent.providers.base import ToolCall
        
        # Register small output tool
        async def small_output_tool():
            return "small result"
        
        setup["registry"].register(
            name="small_output",
            description="Returns small output",
            parameters={"type": "object", "properties": {}},
            handler=small_output_tool,
        )
        
        tool_call = ToolCall(
            id="call_1",
            name="small_output",
            arguments={},
        )
        provider = MockProvider([tool_call, "Done"])
        
        loop = AgentLoop(
            provider=provider,
            tools=setup["registry"],
            tape=setup["tape"],
            context=setup["context"],
            consumer=setup["consumer"],
            max_steps=5,
        )
        
        result = await loop.run_turn("Get small output")
        
        # Check that result is not truncated
        entries = setup["tape"].entries()
        tool_results = [e for e in entries if e.kind == "tool_result"]
        
        if tool_results:
            result_content = tool_results[0].payload["result"]
            assert result_content == "small result"
            assert "truncated" not in result_content
