from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any

import pytest

from coding_agent.plugins.parallel_executor import (
    DependencyAnalyzer,
    ParallelExecutorPlugin,
)


class TestDependencyAnalyzer:
    def test_two_reads_different_files_are_parallel(self):
        call1 = {"name": "file_read", "arguments": {"path": "a.py"}}
        call2 = {"name": "file_read", "arguments": {"path": "b.py"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is True

    def test_read_and_write_same_file_are_sequential(self):
        call1 = {"name": "file_read", "arguments": {"path": "a.py"}}
        call2 = {"name": "file_write", "arguments": {"path": "a.py"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is False

    def test_read_and_write_different_files_are_parallel(self):
        call1 = {"name": "file_read", "arguments": {"path": "a.py"}}
        call2 = {"name": "file_write", "arguments": {"path": "b.py"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is True

    def test_two_writes_same_file_are_sequential(self):
        call1 = {"name": "file_write", "arguments": {"path": "a.py"}}
        call2 = {"name": "file_write", "arguments": {"path": "a.py"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is False

    def test_two_writes_different_files_are_sequential(self):
        call1 = {"name": "file_write", "arguments": {"path": "a.py"}}
        call2 = {"name": "file_write", "arguments": {"path": "b.py"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is False

    def test_replace_and_write_same_file_are_sequential(self):
        call1 = {"name": "file_replace", "arguments": {"path": "a.py"}}
        call2 = {"name": "file_write", "arguments": {"path": "a.py"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is False

    def test_read_and_replace_different_files_are_parallel(self):
        call1 = {"name": "file_read", "arguments": {"path": "a.py"}}
        call2 = {"name": "file_replace", "arguments": {"path": "b.py"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is True

    def test_unrelated_tools_are_parallel(self):
        call1 = {"name": "grep", "arguments": {"pattern": "foo"}}
        call2 = {"name": "shell_exec", "arguments": {"command": "ls"}}
        assert DependencyAnalyzer.can_run_in_parallel(call1, call2) is True

    def test_get_file_path_for_file_tools(self):
        assert DependencyAnalyzer.get_file_path("file_read", {"path": "x.py"}) == "x.py"
        assert (
            DependencyAnalyzer.get_file_path("file_write", {"path": "y.py"}) == "y.py"
        )
        assert (
            DependencyAnalyzer.get_file_path("file_replace", {"path": "z.py"}) == "z.py"
        )

    def test_get_file_path_returns_none_for_non_file_tools(self):
        assert DependencyAnalyzer.get_file_path("grep", {"pattern": "foo"}) is None
        assert DependencyAnalyzer.get_file_path("shell_exec", {"command": "ls"}) is None


class TestParallelExecutorPlugin:
    def test_state_key(self):
        async def echo(name: str, arguments: dict[str, Any]) -> str:
            return name

        plugin = ParallelExecutorPlugin(execute_fn=echo)
        assert plugin.state_key == "parallel_executor"

    def test_hooks_include_execute_tools_batch(self):
        async def echo(name: str, arguments: dict[str, Any]) -> str:
            return name

        plugin = ParallelExecutorPlugin(execute_fn=echo)
        hooks = plugin.hooks()
        assert "execute_tools_batch" in hooks

    def test_max_concurrency_default(self):
        async def echo(name: str, arguments: dict[str, Any]) -> str:
            return name

        plugin = ParallelExecutorPlugin(execute_fn=echo)
        assert plugin.max_concurrency == 5

    def test_max_concurrency_configurable(self):
        async def echo(name: str, arguments: dict[str, Any]) -> str:
            return name

        plugin = ParallelExecutorPlugin(execute_fn=echo, max_concurrency=10)
        assert plugin.max_concurrency == 10

    @pytest.mark.asyncio
    async def test_independent_calls_execute_concurrently(self):
        async def slow_execute(name: str, arguments: dict[str, Any]) -> str:
            await asyncio.sleep(0.1)
            return f"result:{arguments.get('path', '')}"

        plugin = ParallelExecutorPlugin(execute_fn=slow_execute, max_concurrency=5)

        tool_calls = [
            {"name": "file_read", "arguments": {"path": "a.py"}},
            {"name": "file_read", "arguments": {"path": "b.py"}},
            {"name": "file_read", "arguments": {"path": "c.py"}},
        ]

        start = time.monotonic()
        results = await plugin.execute_batch(tool_calls=tool_calls)
        elapsed = time.monotonic() - start

        assert elapsed < 0.25, f"Expected <250ms, got {elapsed * 1000:.0f}ms"
        assert len(results) == 3
        assert results[0] == "result:a.py"
        assert results[1] == "result:b.py"
        assert results[2] == "result:c.py"

    @pytest.mark.asyncio
    async def test_dependent_calls_execute_sequentially(self):
        execution_order: list[str] = []

        async def tracking_execute(name: str, arguments: dict[str, Any]) -> str:
            path = arguments.get("path", "?")
            execution_order.append(f"{name}:{path}:start")
            await asyncio.sleep(0.05)
            execution_order.append(f"{name}:{path}:end")
            return f"ok:{name}:{path}"

        plugin = ParallelExecutorPlugin(execute_fn=tracking_execute, max_concurrency=5)

        tool_calls = [
            {"name": "file_read", "arguments": {"path": "a.py"}},
            {"name": "file_write", "arguments": {"path": "a.py"}},
        ]

        results = await plugin.execute_batch(tool_calls=tool_calls)

        assert len(results) == 2
        assert results[0] == "ok:file_read:a.py"
        assert results[1] == "ok:file_write:a.py"

        read_end = execution_order.index("file_read:a.py:end")
        write_start = execution_order.index("file_write:a.py:start")
        assert read_end < write_start, (
            f"Read should finish before write starts: {execution_order}"
        )

    @pytest.mark.asyncio
    async def test_mixed_independent_and_dependent(self):
        timestamps: dict[str, float] = {}

        async def timed_execute(name: str, arguments: dict[str, Any]) -> str:
            path = arguments.get("path", "?")
            key = f"{name}:{path}"
            timestamps[f"{key}:start"] = time.monotonic()
            await asyncio.sleep(0.05)
            timestamps[f"{key}:end"] = time.monotonic()
            return f"ok:{key}"

        plugin = ParallelExecutorPlugin(execute_fn=timed_execute, max_concurrency=5)

        tool_calls = [
            {"name": "file_read", "arguments": {"path": "a.py"}},
            {"name": "file_read", "arguments": {"path": "b.py"}},
            {"name": "file_write", "arguments": {"path": "a.py"}},
        ]

        results = await plugin.execute_batch(tool_calls=tool_calls)

        assert len(results) == 3
        assert results[0] == "ok:file_read:a.py"
        assert results[1] == "ok:file_read:b.py"
        assert results[2] == "ok:file_write:a.py"

        read_a_start = timestamps["file_read:a.py:start"]
        read_b_start = timestamps["file_read:b.py:start"]
        assert abs(read_a_start - read_b_start) < 0.03, (
            "Reads should start ~simultaneously"
        )

        read_a_end = timestamps["file_read:a.py:end"]
        write_a_start = timestamps["file_write:a.py:start"]
        assert write_a_start >= read_a_end - 0.001, (
            "Write should start after conflicting read finishes"
        )

    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty(self):
        async def noop(name: str, arguments: dict[str, Any]) -> str:
            return "noop"

        plugin = ParallelExecutorPlugin(execute_fn=noop)
        results = await plugin.execute_batch(tool_calls=[])
        assert results == []

    @pytest.mark.asyncio
    async def test_none_tool_calls_returns_empty(self):
        async def noop(name: str, arguments: dict[str, Any]) -> str:
            return "noop"

        plugin = ParallelExecutorPlugin(execute_fn=noop)
        results = await plugin.execute_batch(tool_calls=None)
        assert results == []

    @pytest.mark.asyncio
    async def test_single_call(self):
        async def echo(name: str, arguments: dict[str, Any]) -> str:
            return f"echo:{name}"

        plugin = ParallelExecutorPlugin(execute_fn=echo)
        results = await plugin.execute_batch(
            tool_calls=[{"name": "file_read", "arguments": {"path": "x.py"}}]
        )
        assert results == ["echo:file_read"]

    @pytest.mark.asyncio
    async def test_execute_fn_error_returns_error_string(self):
        async def failing(name: str, arguments: dict[str, Any]) -> str:
            raise RuntimeError("boom")

        plugin = ParallelExecutorPlugin(execute_fn=failing)
        results = await plugin.execute_batch(
            tool_calls=[{"name": "file_read", "arguments": {"path": "x.py"}}]
        )
        assert len(results) == 1
        assert "error" in results[0].lower()
        assert "boom" in results[0].lower()

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def counting_execute(name: str, arguments: dict[str, Any]) -> str:
            nonlocal concurrent_count, max_concurrent
            async with lock:
                concurrent_count += 1
                if concurrent_count > max_concurrent:
                    max_concurrent = concurrent_count
            await asyncio.sleep(0.05)
            async with lock:
                concurrent_count -= 1
            return "ok"

        plugin = ParallelExecutorPlugin(execute_fn=counting_execute, max_concurrency=2)

        tool_calls = [
            {"name": "grep", "arguments": {"pattern": f"p{i}"}} for i in range(4)
        ]

        await plugin.execute_batch(tool_calls=tool_calls)
        assert max_concurrent <= 2, f"Max concurrent was {max_concurrent}, expected ≤2"

    @pytest.mark.asyncio
    async def test_execute_batch_preserves_contextvars_for_parallel_tasks(self):
        structured = ContextVar("structured", default=False)

        async def read_structured(
            name: str, arguments: dict[str, Any]
        ) -> dict[str, bool]:
            await asyncio.sleep(0)
            return {"structured": structured.get()}

        plugin = ParallelExecutorPlugin(execute_fn=read_structured)

        token = structured.set(True)
        try:
            results = await plugin.execute_batch(
                tool_calls=[
                    {"name": "file_read", "arguments": {"path": "a.py"}},
                    {"name": "file_read", "arguments": {"path": "b.py"}},
                ]
            )
        finally:
            structured.reset(token)

        assert results == [{"structured": True}, {"structured": True}]

    @pytest.mark.asyncio
    async def test_execute_batch_forwards_ctx_when_execute_fn_supports_it(self):
        captured_ctxs: list[object | None] = []
        ctx = object()

        async def execute_with_ctx(
            name: str,
            arguments: dict[str, Any],
            *,
            ctx: object | None = None,
        ) -> str:
            del name, arguments
            captured_ctxs.append(ctx)
            return "ok"

        plugin = ParallelExecutorPlugin(execute_fn=execute_with_ctx)

        results = await plugin.execute_batch(
            tool_calls=[
                {"name": "file_read", "arguments": {"path": "a.py"}},
                {"name": "file_read", "arguments": {"path": "b.py"}},
            ],
            ctx=ctx,
        )

        assert results == ["ok", "ok"]
        assert captured_ctxs == [ctx, ctx]
