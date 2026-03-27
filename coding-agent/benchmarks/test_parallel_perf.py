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
    print("=" * 60)
    print("Parallel Execution Benchmark")
    print("=" * 60)
    
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
