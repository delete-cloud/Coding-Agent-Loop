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
