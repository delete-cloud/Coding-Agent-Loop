from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

_FILE_TOOLS = frozenset({"file_read", "file_write", "file_replace"})

_CONFLICT_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("file_write", "file_write"),
        ("file_replace", "file_write"),
        ("file_write", "file_replace"),
        ("file_replace", "file_replace"),
        ("file_read", "file_write"),
        ("file_write", "file_read"),
        ("file_read", "file_replace"),
        ("file_replace", "file_read"),
    }
)

_READ_WRITE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("file_read", "file_write"),
        ("file_write", "file_read"),
        ("file_read", "file_replace"),
        ("file_replace", "file_read"),
    }
)

ToolCallDict = dict[str, Any]
ExecuteFn = Callable[[str, dict[str, Any]], Awaitable[str]]


class DependencyAnalyzer:
    @staticmethod
    def get_file_path(tool_name: str, args: dict[str, Any]) -> str | None:
        if tool_name in _FILE_TOOLS:
            return args.get("path")
        return None

    @classmethod
    def can_run_in_parallel(cls, call1: ToolCallDict, call2: ToolCallDict) -> bool:
        pair = (call1["name"], call2["name"])
        if pair not in _CONFLICT_PAIRS:
            return True

        path1 = cls.get_file_path(call1["name"], call1["arguments"])
        path2 = cls.get_file_path(call2["name"], call2["arguments"])

        if path1 and path2 and path1 == path2:
            return False

        if pair in _READ_WRITE_PAIRS and path1 != path2:
            return True

        return False


class ParallelExecutorPlugin:
    state_key = "parallel_executor"

    def __init__(
        self,
        execute_fn: ExecuteFn,
        max_concurrency: int = 5,
    ) -> None:
        self.execute_fn = execute_fn
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"execute_tools_batch": self.execute_batch}

    def _group_by_dependencies(
        self, tool_calls: list[ToolCallDict]
    ) -> list[list[tuple[int, ToolCallDict]]]:
        if not tool_calls:
            return []

        batches: list[list[tuple[int, ToolCallDict]]] = []
        remaining = list(enumerate(tool_calls))

        while remaining:
            current_batch: list[tuple[int, ToolCallDict]] = [remaining[0]]
            consumed_indices: set[int] = {0}

            for i in range(1, len(remaining)):
                if i in consumed_indices:
                    continue
                _, candidate = remaining[i]
                can_add = all(
                    DependencyAnalyzer.can_run_in_parallel(existing, candidate)
                    for _, existing in current_batch
                )
                if can_add:
                    current_batch.append(remaining[i])
                    consumed_indices.add(i)

            batches.append(current_batch)
            remaining = [
                item
                for idx, item in enumerate(remaining)
                if idx not in consumed_indices
            ]

        return batches

    async def execute_batch(
        self, tool_calls: list[ToolCallDict] | None = None, **kwargs: Any
    ) -> list[str]:
        if not tool_calls:
            return []

        batches = self._group_by_dependencies(tool_calls)
        results_by_index: dict[int, str] = {}

        for batch in batches:

            async def _run_one(idx: int, call: ToolCallDict) -> tuple[int, str]:
                async with self._semaphore:
                    try:
                        result = await self.execute_fn(call["name"], call["arguments"])
                        return idx, result
                    except Exception as exc:
                        return idx, json.dumps({"error": str(exc)})

            batch_results = await asyncio.gather(
                *[_run_one(idx, call) for idx, call in batch]
            )
            for idx, result in batch_results:
                results_by_index[idx] = result

        return [results_by_index[i] for i in range(len(tool_calls))]
