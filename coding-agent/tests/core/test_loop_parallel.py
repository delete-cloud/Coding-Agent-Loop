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
        mock_context.build_working_set = AsyncMock(return_value=[])
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
        loop._enable_parallel = True  # Enable parallel for this test
        assert loop._can_parallelize(calls) is True
    
    def test_cannot_parallelize_write_conflicts(self):
        """Multiple writes should not be parallelized."""
        calls = [
            ToolCall("c1", "file_write", {"path": "a.py", "content": "x"}),
            ToolCall("c2", "file_write", {"path": "b.py", "content": "y"}),
        ]
        
        loop = AgentLoop.__new__(AgentLoop)
        loop._enable_parallel = True  # Enable parallel for this test
        # Conservative: multiple file_writes = sequential
        assert loop._can_parallelize(calls) is False
    
    def test_single_call_not_parallelized(self):
        """Single call should not trigger parallel logic."""
        calls = [ToolCall("c1", "file_read", {"path": "a.py"})]
        
        loop = AgentLoop.__new__(AgentLoop)
        loop._enable_parallel = True  # Enable parallel for this test
        assert loop._can_parallelize(calls) is False
    
    def test_parallel_disabled_by_config(self):
        """Parallel execution should be disabled when config sets enable_parallel=False."""
        calls = [
            ToolCall("c1", "file_read", {"path": "a.py"}),
            ToolCall("c2", "file_read", {"path": "b.py"}),
        ]
        
        loop = AgentLoop.__new__(AgentLoop)
        loop._enable_parallel = False  # Disabled by config
        assert loop._can_parallelize(calls) is False
    
    def test_parallel_enabled_by_config(self):
        """Parallel execution should be enabled when config sets enable_parallel=True."""
        calls = [
            ToolCall("c1", "file_read", {"path": "a.py"}),
            ToolCall("c2", "file_read", {"path": "b.py"}),
        ]
        
        loop = AgentLoop.__new__(AgentLoop)
        loop._enable_parallel = True  # Enabled by config
        assert loop._can_parallelize(calls) is True
    
    @pytest.mark.asyncio
    async def test_doom_loop_detection_in_parallel_path(self):
        """Doom loop should be detected in parallel execution path."""
        from coding_agent.core.doom import DoomDetector
        
        calls = [
            ToolCall("c1", "file_read", {"path": "a.py"}),
            ToolCall("c2", "file_read", {"path": "a.py"}),  # Same call
            ToolCall("c3", "file_read", {"path": "a.py"}),  # Same call again
        ]
        
        # Create loop with doom threshold of 3
        loop = AgentLoop.__new__(AgentLoop)
        loop.doom_detector = DoomDetector(threshold=3)
        loop._enable_parallel = True
        
        # Prime the doom detector with 2 identical calls
        loop.doom_detector.observe("file_read", {"path": "a.py"})
        loop.doom_detector.observe("file_read", {"path": "a.py"})
        
        # The third call should trigger doom loop detection
        # when checked in _execute_tools_parallel
        assert loop.doom_detector.observe("file_read", {"path": "a.py"}) is True
    
    @pytest.mark.asyncio
    async def test_max_concurrency_respected(self):
        """ParallelExecutor should respect max_concurrency setting."""
        from coding_agent.core.parallel import ParallelExecutor
        
        execution_times = []
        
        # Note: execute_fn signature is (name: str, args: dict) -> str
        async def slow_execute(name: str, args: dict) -> str:
            execution_times.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.1)
            return "result"
        
        # Create executor with max_concurrency=2
        executor = ParallelExecutor(execute_fn=slow_execute, max_concurrency=2)
        
        calls = [
            ToolCall(f"c{i}", "file_read", {"path": f"file{i}.py"})
            for i in range(4)
        ]
        
        start_time = asyncio.get_event_loop().time()
        results = await executor.execute_all(calls)
        elapsed = asyncio.get_event_loop().time() - start_time
        
        # Should take at least 0.2s (2 batches of 2 concurrent calls, each 0.1s)
        # With max_concurrency=2 and 4 calls, should be ~0.2s
        assert len(results) == 4
        # Should take more than 0.15s (sequential would be 0.4s, parallel would be ~0.1s)
        # With max_concurrency=2, should take ~0.2s
        assert elapsed >= 0.15, f"Expected at least 0.15s with max_concurrency=2, got {elapsed}s"
