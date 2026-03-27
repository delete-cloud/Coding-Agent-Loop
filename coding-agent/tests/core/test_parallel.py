"""Tests for parallel tool execution."""

import pytest
import asyncio
from coding_agent.core.parallel import (
    DependencyAnalyzer,
    ParallelExecutor,
    ExecutionResult,
)
from coding_agent.providers.base import ToolCall


class TestDependencyAnalyzer:
    def test_same_file_read_write_conflict(self):
        """Reading and writing same file cannot be parallel."""
        call1 = ToolCall("c1", "file_read", {"path": "test.py"})
        call2 = ToolCall("c2", "file_write", {"path": "test.py", "content": "x"})
        assert not DependencyAnalyzer.can_run_in_parallel(call1, call2)
    
    def test_different_file_read_write_ok(self):
        """Reading A and writing B can be parallel."""
        call1 = ToolCall("c1", "file_read", {"path": "a.py"})
        call2 = ToolCall("c2", "file_write", {"path": "b.py", "content": "x"})
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2)
    
    def test_multiple_reads_ok(self):
        """Multiple file reads can be parallel."""
        call1 = ToolCall("c1", "file_read", {"path": "a.py"})
        call2 = ToolCall("c2", "file_read", {"path": "b.py"})
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2)
    
    def test_same_file_write_conflict(self):
        """Two writes to same file cannot be parallel."""
        call1 = ToolCall("c1", "file_write", {"path": "test.py", "content": "x"})
        call2 = ToolCall("c2", "file_write", {"path": "test.py", "content": "y"})
        assert not DependencyAnalyzer.can_run_in_parallel(call1, call2)


class TestParallelExecutor:
    @pytest.mark.asyncio
    async def test_single_call_no_parallel(self):
        """Single call should not use parallel logic."""
        async def execute(name, args):
            return f"{name} result"
        
        executor = ParallelExecutor(execute)
        calls = [ToolCall("c1", "test", {"x": 1})]
        
        results = await executor.execute_all(calls)
        assert len(results) == 1
        assert results[0].result == "test result"
    
    @pytest.mark.asyncio
    async def test_multiple_independent_calls(self):
        """Multiple independent calls should execute in parallel."""
        execution_order = []
        
        async def execute(name, args):
            execution_order.append(name)
            await asyncio.sleep(0.01)  # Simulate work
            return f"{name} result"
        
        executor = ParallelExecutor(execute)
        calls = [
            ToolCall("c1", "file_read", {"path": "a.py"}),
            ToolCall("c2", "file_read", {"path": "b.py"}),
            ToolCall("c3", "file_read", {"path": "c.py"}),
        ]
        
        results = await executor.execute_all(calls)
        
        # All should complete
        assert len(results) == 3
        # Results should be in original order
        assert results[0].index == 0
        assert results[1].index == 1
        assert results[2].index == 2
    
    @pytest.mark.asyncio
    async def test_dependent_calls_sequential_batches(self):
        """Dependent calls should be in separate batches."""
        async def execute(name, args):
            return f"{name}:{args.get('path', 'x')}"
        
        executor = ParallelExecutor(execute)
        # Read and write same file - should not be parallel
        calls = [
            ToolCall("c1", "file_read", {"path": "test.py"}),
            ToolCall("c2", "file_write", {"path": "test.py", "content": "new"}),
        ]
        
        batches = executor._group_by_dependencies(calls)
        # Should be 2 batches (sequential)
        assert len(batches) == 2
    
    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Errors in one call shouldn't affect others."""
        async def execute(name, args):
            if name == "fail":
                raise ValueError("Failed!")
            return "success"
        
        executor = ParallelExecutor(execute)
        calls = [
            ToolCall("c1", "ok", {}),
            ToolCall("c2", "fail", {}),
            ToolCall("c3", "ok", {}),
        ]
        
        results = await executor.execute_all(calls)
        
        assert len(results) == 3
        assert results[0].error is None
        assert results[1].error is not None
        assert results[2].error is None

    @pytest.mark.asyncio
    async def test_batch_ordering_preserved(self):
        """Verify results are in original tool_calls order across multiple batches."""
        execution_order = []
        
        async def execute(name, args):
            # Simulate different execution times
            if name == "slow":
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0.01)
            execution_order.append(name)
            return f"{name}_result"
        
        executor = ParallelExecutor(execute, max_concurrency=2)
        # Mix of independent and dependent calls to create multiple batches
        calls = [
            ToolCall("c1", "fast", {"path": "a.py"}),
            ToolCall("c2", "file_write", {"path": "x.py", "content": "x"}),
            ToolCall("c3", "slow", {}),
            ToolCall("c4", "file_write", {"path": "x.py", "content": "y"}),  # Same file, separate batch
            ToolCall("c5", "fast", {}),
        ]
        
        results = await executor.execute_all(calls)
        
        # Results should be in original order, not execution order
        assert len(results) == 5
        assert results[0].tool_call.id == "c1"
        assert results[1].tool_call.id == "c2"
        assert results[2].tool_call.id == "c3"
        assert results[3].tool_call.id == "c4"
        assert results[4].tool_call.id == "c5"
        
        # Verify results are correctly associated
        assert results[0].result == "fast_result"
        assert results[1].result == "file_write_result"
        assert results[2].result == "slow_result"
        assert results[3].result == "file_write_result"
        assert results[4].result == "fast_result"

    @pytest.mark.asyncio
    async def test_result_truncation_in_parallel(self):
        """Verify long results are truncated in parallel execution."""
        async def execute(name, args):
            if name == "long":
                return "x" * 15000  # Exceeds MAX_RESULT_SIZE
            return "short"
        
        executor = ParallelExecutor(execute)
        calls = [
            ToolCall("c1", "long", {}),
            ToolCall("c2", "short", {}),
        ]
        
        results = await executor.execute_all(calls)
        
        assert len(results) == 2
        # Long result should be truncated
        assert len(results[0].result) <= 10000 + 50  # MAX_RESULT_SIZE + truncation message
        assert "truncated" in results[0].result
        assert results[0].result.startswith("x" * 100)
        # Short result should be unchanged
        assert results[1].result == "short"
