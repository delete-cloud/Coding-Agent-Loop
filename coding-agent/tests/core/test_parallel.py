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
