# Parallel Tool Execution - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans

**Goal:** Enable concurrent execution of independent tool calls to reduce latency when agent needs to perform multiple operations.

**Architecture:** Modify `AgentLoop` to detect when multiple tool calls are independent (no data dependencies) and execute them concurrently using `asyncio.gather()`. Maintain order for recording to tape.

**Performance Target:** 
- Sequential: 3 file reads = 3 × 100ms = 300ms
- Parallel: 3 file reads = 100ms (concurrent)

---

## Key Design Decisions

### Dependency Detection

**Independent calls (can parallelize):**
- `file_read(path="a.py")` + `file_read(path="b.py")` - different files
- `grep(pattern="foo")` + `file_read(path="x.py")` - no data dependency
- `bash("ls")` + `file_read(path="y.py")` - no dependency

**Dependent calls (must be sequential):**
- `file_read(path="x.py")` → `file_replace(path="x.py", ...)` - same file
- Tool calls where args reference previous result

### Execution Strategy

```python
# Sequential (current)
for call in tool_calls:
    result = await execute(call)  # One at a time

# Parallel (target)
independent_calls = group_by_independence(tool_calls)
results = await asyncio.gather(*[
    execute(call) for call in independent_calls
])
```

### Tape Recording

Even with parallel execution, results must be recorded to tape in the order tool_calls were received (LLM expects this ordering).

---

## File Map

```
coding-agent/
  src/coding_agent/
    core/
      parallel.py              # NEW: Parallel execution utilities
      loop.py                  # MOD: Integrate parallel execution
    tools/
      registry.py              # MOD: Add dependency analysis
  tests/
    core/
      test_parallel.py         # NEW: Parallel execution tests
      test_loop_parallel.py    # NEW: Integration tests
```

---

## Task 1: Create Parallel Execution Utilities

**Files:**
- Create: `coding-agent/src/coding_agent/core/parallel.py`

- [ ] **Step 1: Create dependency analyzer and parallel executor**

`coding-agent/src/coding_agent/core/parallel.py`:

```python
"""Parallel tool execution utilities."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from coding_agent.providers.base import ToolCall


@dataclass
class ExecutionResult:
    """Result of a tool execution with ordering info."""
    index: int
    tool_call: ToolCall
    result: str
    error: str | None = None


class DependencyAnalyzer:
    """Analyzes tool call dependencies to determine what can run in parallel."""
    
    # Tool pairs that conflict (cannot run in parallel)
    # Format: {(tool1, tool2): "reason"}
    CONFLICT_PAIRS: set[tuple[str, str]] = frozenset({
        # Same file operations conflict
        ("file_write", "file_write"),
        ("file_replace", "file_write"),
        ("file_write", "file_replace"),
        ("file_replace", "file_replace"),
        # Read during write is unsafe
        ("file_read", "file_write"),
        ("file_write", "file_read"),
        ("file_read", "file_replace"),
        ("file_replace", "file_read"),
    })
    
    @classmethod
    def get_file_path(cls, tool_name: str, args: dict[str, Any]) -> str | None:
        """Extract file path from tool arguments if applicable."""
        if tool_name in ("file_read", "file_write", "file_replace"):
            return args.get("path")
        return None
    
    @classmethod
    def can_run_in_parallel(
        cls, 
        call1: ToolCall, 
        call2: ToolCall
    ) -> bool:
        """Check if two tool calls can be executed in parallel.
        
        Returns True if:
        - Tools don't conflict by type
        - Don't operate on the same file (if file operations)
        """
        # Check general tool type conflicts
        pair = (call1.name, call2.name)
        if pair in cls.CONFLICT_PAIRS:
            # Check if it's the same file
            path1 = cls.get_file_path(call1.name, call1.arguments)
            path2 = cls.get_file_path(call2.name, call2.arguments)
            
            # Same file operation conflicts
            if path1 and path2 and path1 == path2:
                return False
            
            # Different files: check if still conflict
            # e.g., file_read + file_write on different files is OK
            if pair in {("file_read", "file_write"), ("file_write", "file_read"),
                       ("file_read", "file_replace"), ("file_replace", "file_read")}:
                if path1 != path2:
                    return True
            
            return False
        
        return True


class ParallelExecutor:
    """Executes tool calls with automatic parallelization."""
    
    def __init__(
        self,
        execute_fn: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_concurrency: int = 5,
    ):
        self.execute_fn = execute_fn
        self.max_concurrency = max_concurrency
        self.analyzer = DependencyAnalyzer()
    
    def _group_by_dependencies(
        self, 
        tool_calls: list[ToolCall]
    ) -> list[list[tuple[int, ToolCall]]]:
        """Group tool calls into batches that can run in parallel.
        
        Returns list of batches, where each batch contains (index, call) tuples.
        Maintains original order within constraints.
        """
        if not tool_calls:
            return []
        
        batches: list[list[tuple[int, ToolCall]]] = []
        remaining = list(enumerate(tool_calls))
        
        while remaining:
            # Start a new batch with the first remaining call
            current_batch = [remaining[0]]
            batch_indices = {0}  # Index in remaining list
            
            # Try to add more calls to this batch
            for i, (idx, call) in enumerate(remaining[1:], start=1):
                if i in batch_indices:
                    continue
                
                # Check if this call conflicts with any in current batch
                can_add = True
                for _, batch_call in current_batch:
                    if not self.analyzer.can_run_in_parallel(batch_call, call):
                        can_add = False
                        break
                
                if can_add:
                    current_batch.append((idx, call))
                    batch_indices.add(i)
            
            batches.append(current_batch)
            # Remove processed calls from remaining
            remaining = [r for i, r in enumerate(remaining) if i not in batch_indices]
        
        return batches
    
    async def execute_all(
        self, 
        tool_calls: list[ToolCall]
    ) -> list[ExecutionResult]:
        """Execute all tool calls with automatic parallelization.
        
        Returns results in original tool_calls order.
        """
        if not tool_calls:
            return []
        
        if len(tool_calls) == 1:
            # Single call - no parallelization needed
            call = tool_calls[0]
            try:
                result = await self.execute_fn(call.name, call.arguments)
                return [ExecutionResult(0, call, result)]
            except Exception as e:
                import json
                return [ExecutionResult(0, call, json.dumps({"error": str(e)}), str(e))]
        
        # Group into parallel batches
        batches = self._group_by_dependencies(tool_calls)
        
        all_results: list[ExecutionResult] = []
        
        for batch in batches:
            # Execute batch concurrently
            async def execute_one(idx: int, call: ToolCall) -> ExecutionResult:
                try:
                    result = await self.execute_fn(call.name, call.arguments)
                    return ExecutionResult(idx, call, result)
                except Exception as e:
                    import json
                    return ExecutionResult(
                        idx, call, 
                        json.dumps({"error": str(e)}),
                        str(e)
                    )
            
            # Run batch in parallel
            batch_results = await asyncio.gather(*[
                execute_one(idx, call) for idx, call in batch
            ])
            
            all_results.extend(batch_results)
        
        # Sort by original index to maintain order
        all_results.sort(key=lambda r: r.index)
        
        return all_results
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/core/parallel.py
git commit -m "feat(core): add parallel tool execution utilities"
```

---

## Task 2: Integrate Parallel Execution into AgentLoop

**Files:**
- Modify: `coding-agent/src/coding_agent/core/loop.py`

- [ ] **Step 1: Add parallel execution support to AgentLoop**

Modify `coding-agent/src/coding_agent/core/loop.py`:

```python
# Add import at top
from coding_agent.core.parallel import ParallelExecutor

# In AgentLoop.__init__, add:
self._parallel_executor = ParallelExecutor(
    execute_fn=self.tools.execute,
    max_concurrency=5,
)

# In run_turn, modify tool execution section:
if response.has_tool_calls:
    # Check if we should use parallel execution
    use_parallel = len(response.tool_calls) > 1 and self._can_parallelize(response.tool_calls)
    
    if use_parallel:
        results = await self._execute_tools_parallel(response.tool_calls)
    else:
        results = await self._execute_tools_sequential(response.tool_calls)
```

Add these methods to `AgentLoop`:

```python
def _can_parallelize(self, tool_calls: list[ToolCall]) -> bool:
    """Check if tool calls can be parallelized."""
    if len(tool_calls) <= 1:
        return False
    
    # Check if any are high-risk and should be sequential
    high_risk = {"file_write", "file_replace", "bash"}
    risky_count = sum(1 for call in tool_calls if call.name in high_risk)
    
    # If multiple risky operations, be conservative
    if risky_count > 1:
        return False
    
    return True

async def _execute_tools_parallel(
    self, 
    tool_calls: list[ToolCall]
) -> list[tuple[ToolCall, str]]:
    """Execute tools in parallel where possible."""
    from coding_agent.wire import ToolCallBegin, ToolCallEnd
    
    # Emit begin events for all calls
    for call in tool_calls:
        self.tape.append(Entry.tool_call(call.id, call.name, call.arguments))
        await self.consumer.emit(ToolCallBegin(
            call_id=call.id,
            tool=call.name,
            args=call.arguments,
        ))
    
    # Execute in parallel
    results = await self._parallel_executor.execute_all(tool_calls)
    
    # Record results and emit end events (in original order)
    output: list[tuple[ToolCall, str]] = []
    for result in results:
        call = result.tool_call
        result_str = result.result
        
        # Truncate if needed
        MAX_RESULT_SIZE = 10000
        if len(result_str) > MAX_RESULT_SIZE:
            result_str = result_str[:MAX_RESULT_SIZE] + f"\n... ({len(result_str) - MAX_RESULT_SIZE} chars truncated)"
        
        self.tape.append(Entry.tool_result(call.id, result_str))
        await self.consumer.emit(ToolCallEnd(
            call_id=call.id,
            result=result_str,
        ))
        
        output.append((call, result_str))
    
    return output

async def _execute_tools_sequential(
    self,
    tool_calls: list[ToolCall]
) -> list[tuple[ToolCall, str]]:
    """Execute tools sequentially (original behavior)."""
    # Keep existing sequential logic
    output = []
    for call in tool_calls:
        result = await self._execute_single_tool(call)
        output.append((call, result))
    return output
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/core/loop.py
git commit -m "feat(loop): integrate parallel tool execution"
```

---

## Task 3: Add Configuration Option

**Files:**
- Modify: `coding-agent/src/coding_agent/core/config.py`
- Modify: `coding-agent/src/coding_agent/__main__.py`

- [ ] **Step 1: Add parallel execution config**

Add to `Config` class:

```python
# Execution
enable_parallel_tools: bool = True
max_parallel_tools: int = 5
```

Add to `_run()` in `__main__.py`:

```python
@click.option("--parallel/--no-parallel", default=True, help="Enable parallel tool execution")
@click.option("--max-parallel", default=5, help="Maximum parallel tool executions")
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/core/config.py src/coding_agent/__main__.py
git commit -m "feat(config): add parallel execution configuration"
```

---

## Task 4: Write Tests

**Files:**
- Create: `coding-agent/tests/core/test_parallel.py`
- Create: `coding-agent/tests/core/test_loop_parallel.py`

- [ ] **Step 1: Write parallel execution unit tests**

`coding-agent/tests/core/test_parallel.py`:

```python
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
```

- [ ] **Step 2: Write integration tests**

`coding-agent/tests/core/test_loop_parallel.py`:

```python
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
```

- [ ] **Step 3: Run tests and commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_parallel.py tests/core/test_loop_parallel.py -v
git add tests/core/test_parallel.py tests/core/test_loop_parallel.py
git commit -m "test(core): add parallel execution tests"
```

---

## Task 5: Performance Benchmark

**Files:**
- Create: `coding-agent/benchmarks/test_parallel_perf.py`

- [ ] **Step 1: Create performance benchmark**

```python
#!/usr/bin/env python3
"""Benchmark parallel vs sequential tool execution."""

import asyncio
import time
from coding_agent.core.parallel import ParallelExecutor
from coding_agent.providers.base import ToolCall


async def mock_tool_call(name, args):
    """Simulate a tool call with 100ms delay."""
    await asyncio.sleep(0.1)  # 100ms
    return f"{name} result"


async def benchmark():
    print("Parallel Execution Benchmark")
    print("=" * 50)
    
    # Sequential execution
    print("\nSequential execution (3 file reads):")
    calls = [
        ToolCall(f"c{i}", "file_read", {"path": f"file{i}.py"})
        for i in range(3)
    ]
    
    start = time.time()
    for call in calls:
        await mock_tool_call(call.name, call.arguments)
    seq_time = time.time() - start
    print(f"  Time: {seq_time:.3f}s")
    
    # Parallel execution
    print("\nParallel execution (3 file reads):")
    executor = ParallelExecutor(mock_tool_call)
    
    start = time.time()
    results = await executor.execute_all(calls)
    par_time = time.time() - start
    print(f"  Time: {par_time:.3f}s")
    
    print(f"\nSpeedup: {seq_time/par_time:.2f}x")
    print(f"Results: {len(results)} calls completed")


if __name__ == "__main__":
    asyncio.run(benchmark())
```

- [ ] **Step 2: Run benchmark and commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
mkdir -p benchmarks
uv run python benchmarks/test_parallel_perf.py
git add benchmarks/test_parallel_perf.py
git commit -m "bench: add parallel execution performance benchmark"
```

---

## Task 6: Documentation

**Files:**
- Modify: `coding-agent/README.md`

- [ ] **Step 1: Document parallel execution feature**

Add to README:

```markdown
## Parallel Tool Execution

The agent automatically executes independent tool calls in parallel to reduce latency:

```bash
# 3 file reads that would take 300ms sequentially → 100ms in parallel
coding-agent run --goal "Read file1.py, file2.py, and file3.py"
```

### Configuration

```bash
# Disable parallel execution
uv run python -m coding_agent run --goal "..." --no-parallel

# Configure max parallelism (default: 5)
uv run python -m coding_agent run --goal "..." --max-parallel 10
```

### Safety

The agent detects dependencies and only parallelizes safe operations:
- ✅ Parallel: `file_read(a) + file_read(b)`
- ✅ Parallel: `file_read(a) + grep(pattern)`
- ❌ Sequential: `file_read(a) + file_write(a)` (same file)
- ❌ Sequential: `file_write(a) + file_write(a)` (conflict risk)
```

- [ ] **Step 2: Final commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/ -v
git add README.md
git commit -m "docs: document parallel tool execution feature"
```

---

## Summary

| Task | Component | Changes |
|------|-----------|---------|
| 1 | Parallel utilities | Dependency analyzer, batch executor |
| 2 | AgentLoop integration | Parallel execution in tool handling |
| 3 | Configuration | --parallel/--max-parallel flags |
| 4 | Tests | Unit + integration tests |
| 5 | Benchmark | Performance verification |
| 6 | Documentation | README updates |

Expected performance improvement: **2-3x faster** for multi-file operations.
