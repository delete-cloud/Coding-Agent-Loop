"""Integration tests for parallel execution in AgentLoop."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from coding_agent.core.loop import AgentLoop
from coding_agent.providers.base import ToolCall, StreamingResponse


class TestAgentLoopParallel:
    @pytest.mark.asyncio
    async def test_parallel_execution_enabled(self):
        """Loop should use parallel execution for independent calls."""
        # Mock provider that returns multiple tool calls
        mock_provider = MagicMock()
        mock_provider.model_name = "mock"
        mock_provider.max_context_size = 1000
        
        async def mock_stream(*args, **kwargs):
            yield MagicMock(type="tool_call", tool_call=ToolCall("c1", "file_read", {"path": "a.py"}))
            yield MagicMock(type="tool_call", tool_call=ToolCall("c2", "file_read", {"path": "b.py"}))
            yield MagicMock(type="done")
        
        mock_provider.stream = mock_stream
        
        # Mock tools
        mock_tools = MagicMock()
        mock_tools.execute = AsyncMock(return_value="file content")
        mock_tools.schemas.return_value = []
        
        # Mock other components
        mock_tape = MagicMock()
        mock_context = MagicMock()
        mock_context.build_working_set.return_value = []
        mock_consumer = AsyncMock()
        
        loop = AgentLoop(
            provider=mock_provider,
            tools=mock_tools,
            tape=mock_tape,
            context=mock_context,
            consumer=mock_consumer,
        )
        
        # Check that parallel executor is set up
        assert loop._parallel_executor is not None
    
    def test_can_parallelize_multiple_reads(self):
        """Multiple file reads should be parallelizable."""
        calls = [
            ToolCall("c1", "file_read", {"path": "a.py"}),
            ToolCall("c2", "file_read", {"path": "b.py"}),
            ToolCall("c3", "file_read", {"path": "c.py"}),
        ]
        
        # Create minimal loop to test method
        loop = AgentLoop.__new__(AgentLoop)
        assert loop._can_parallelize(calls) is True
    
    def test_cannot_parallelize_write_conflicts(self):
        """Multiple writes should not be parallelized."""
        calls = [
            ToolCall("c1", "file_write", {"path": "a.py", "content": "x"}),
            ToolCall("c2", "file_write", {"path": "b.py", "content": "y"}),
        ]
        
        loop = AgentLoop.__new__(AgentLoop)
        # Conservative: multiple file_writes = sequential
        assert loop._can_parallelize(calls) is False
    
    def test_single_call_not_parallelized(self):
        """Single call should not trigger parallel logic."""
        calls = [ToolCall("c1", "file_read", {"path": "a.py"})]
        
        loop = AgentLoop.__new__(AgentLoop)
        assert loop._can_parallelize(calls) is False
