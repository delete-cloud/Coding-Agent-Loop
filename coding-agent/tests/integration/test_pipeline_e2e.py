"""End-to-end pipeline integration tests — all layers wire together through real Pipeline."""

import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.tape.models import Entry

from coding_agent.__main__ import create_agent
from coding_agent.adapter import PipelineAdapter
from coding_agent.adapter_types import StopReason, TurnOutcome
from coding_agent.wire.protocol import (
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
)

CONFIG_PATH = (
    Path(__file__).parent.parent.parent / "src" / "coding_agent" / "agent.toml"
)


def _skip_if_no_config():
    if not CONFIG_PATH.exists():
        pytest.skip("agent.toml not found")


def _setup_agent(tmp_path, *, approval_mode: str | None = None):
    _skip_if_no_config()
    pipeline, ctx = create_agent(
        config_path=CONFIG_PATH,
        data_dir=tmp_path,
        api_key="sk-test",
        approval_mode_override=approval_mode,
    )
    return pipeline, ctx


def _mock_provider(pipeline, stream_fn):
    mock = AsyncMock()
    mock.stream = stream_fn
    llm_plugin = pipeline._registry.get("llm_provider")
    llm_plugin._instance = mock
    return mock


class TestPipelineE2E:
    @pytest.mark.asyncio
    async def test_subagent_tool_executes_from_real_pipeline_turn(self, tmp_path):
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")
        ctx.config["max_tool_rounds"] = 3

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            del messages, tools, kwargs
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-subagent",
                    name="subagent",
                    arguments={"goal": "Inspect child task"},
                )
                yield DoneEvent()
            elif call_count == 2:
                yield TextEvent(text="Child finished summary")
                yield DoneEvent()
            else:
                yield TextEvent(text="Parent received child result")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("Use a subagent")

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message == "Parent received child result"

        tool_results = ctx.tape.filter("tool_result")
        visible_tool_results = [
            entry for entry in tool_results if not entry.meta.get("skip_context")
        ]
        hidden_child_entries = [
            entry for entry in list(ctx.tape) if entry.meta.get("skip_context")
        ]
        assert len(visible_tool_results) == 1
        assert visible_tool_results[0].payload["content"] == (
            "Subagent completed: Child finished summary"
        )
        assert any(
            entry.kind == "message"
            and entry.payload.get("content") == "Inspect child task"
            for entry in hidden_child_entries
        )
        assert any(
            entry.kind == "message"
            and entry.payload.get("content") == "Child finished summary"
            for entry in hidden_child_entries
        )

    @pytest.mark.asyncio
    async def test_run_command_streaming_events(self, tmp_path):
        """Given mock LLM emitting text+tool+done, when run via adapter with consumer, then StreamDelta/ToolCallDelta/TurnEnd emitted."""
        pipeline, ctx = _setup_agent(tmp_path)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield TextEvent(text="Thinking...")
                yield ToolCallEvent(
                    tool_call_id="tc-001",
                    name="file_read",
                    arguments={"path": "readme.txt"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Here is the answer.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        consumer = AsyncMock()
        consumer.emit = AsyncMock()
        adapter = PipelineAdapter(pipeline, ctx, consumer=consumer)

        outcome = await adapter.run_turn("read the readme")

        assert isinstance(outcome, TurnOutcome)
        assert outcome.final_message is not None
        assert "answer" in outcome.final_message

        emitted = [call.args[0] for call in consumer.emit.call_args_list]
        emitted_types = [type(msg) for msg in emitted]

        assert StreamDelta in emitted_types
        assert ToolCallDelta in emitted_types
        assert TurnEnd in emitted_types

        deltas = [m for m in emitted if isinstance(m, StreamDelta)]
        assert any("Thinking" in d.content for d in deltas)
        assert any("answer" in d.content for d in deltas)

        tool_deltas = [m for m in emitted if isinstance(m, ToolCallDelta)]
        assert len(tool_deltas) >= 1
        assert tool_deltas[0].tool_name == "file_read"
        assert tool_deltas[0].call_id == "tc-001"

        assert isinstance(emitted[-1], TurnEnd)

    @pytest.mark.asyncio
    async def test_repl_two_turn_context_preserved(self, tmp_path):
        """Given 2-turn conversation via PipelineAdapter, when second turn runs, then tape has both exchanges and LLM sees first turn's context."""
        pipeline, ctx = _setup_agent(tmp_path)

        turn_messages_seen: list[list[dict[str, object]]] = []

        async def mock_stream(messages, tools=None, **kwargs):
            turn_messages_seen.append(list(messages))
            yield TextEvent(text=f"Reply to turn {len(turn_messages_seen)}")
            yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)

        outcome1 = await adapter.run_turn("Hello, what is Python?")
        assert outcome1.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome1.final_message is not None
        assert "turn 1" in outcome1.final_message

        outcome2 = await adapter.run_turn("Tell me more about decorators")
        assert outcome2.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome2.final_message is not None
        assert "turn 2" in outcome2.final_message

        all_entries = list(ctx.tape)
        user_entries = [
            e
            for e in all_entries
            if e.kind == "message" and e.payload.get("role") == "user"
        ]
        assistant_entries = [
            e
            for e in all_entries
            if e.kind == "message" and e.payload.get("role") == "assistant"
        ]
        assert len(user_entries) == 2
        assert len(assistant_entries) == 2

        assert len(turn_messages_seen) == 2
        second_call_messages = turn_messages_seen[1]
        user_contents = [
            str(m.get("content", ""))
            for m in second_call_messages
            if m.get("role") == "user"
        ]
        assert any("Python" in c for c in user_contents), (
            "Second turn should see first turn's user message in context"
        )

    @pytest.mark.asyncio
    async def test_headless_mode_stdout(self, tmp_path, capsys):
        """Given HeadlessConsumer + mock LLM, when adapter runs, then streamed text appears on stdout."""
        from coding_agent.ui.headless import HeadlessConsumer

        pipeline, ctx = _setup_agent(tmp_path)

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello from headless mode!")
            yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        consumer = HeadlessConsumer(auto_approve=True)
        adapter = PipelineAdapter(pipeline, ctx, consumer=consumer)

        outcome = await adapter.run_turn("Say hello")

        assert outcome.stop_reason == StopReason.NO_TOOL_CALLS
        assert outcome.final_message == "Hello from headless mode!"

        captured = capsys.readouterr()
        assert "Hello from headless mode!" in captured.out

    @pytest.mark.asyncio
    async def test_doom_detection_triggers(self, tmp_path):
        """Given DoomDetectorPlugin(threshold=3) + LLM emitting same tool call repeatedly, when run completes, then stop_reason is DOOM_LOOP."""
        from coding_agent.plugins.doom_detector import DoomDetectorPlugin

        pipeline, ctx = _setup_agent(tmp_path)

        doom = DoomDetectorPlugin(threshold=3)
        pipeline._registry._plugins[doom.state_key] = doom
        for hook_name, hook_fn in doom.hooks().items():
            pipeline._registry._hook_index[hook_name] = [
                fn
                for fn in pipeline._registry._hook_index.get(hook_name, [])
                if getattr(fn, "__self__", None).__class__ is not DoomDetectorPlugin
            ]
            pipeline._registry._hook_index[hook_name].append(hook_fn)
        pipeline._runtime = HookRuntime(pipeline._registry)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            yield ToolCallEvent(
                tool_call_id=f"tc-{call_count:03d}",
                name="file_read",
                arguments={"path": "stuck.txt"},
            )
            yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("do something")

        assert outcome.stop_reason == StopReason.DOOM_LOOP
        assert call_count >= 3

        tool_calls = ctx.tape.filter("tool_call")
        assert len(tool_calls) >= 3
        assert all(tc.payload["name"] == "file_read" for tc in tool_calls)

    @pytest.mark.asyncio
    async def test_tool_error_recovery(self, tmp_path):
        """Given a tool that raises RuntimeError, when pipeline executes it, then error is recorded in tape and LLM recovers with text response."""
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-err",
                    name="bash_run",
                    arguments={"command": "this_will_fail_in_tool"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="I encountered an error but recovered.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)

        original_call_first = pipeline._runtime.call_first

        def patched_call_first(hook_name, **kwargs):
            if hook_name == "execute_tool" and kwargs.get("name") == "bash_run":
                raise RuntimeError("Tool execution failed: command not found")
            return original_call_first(hook_name, **kwargs)

        pipeline._runtime.call_first = patched_call_first

        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("run a command")

        assert isinstance(outcome, TurnOutcome)

        tool_results = ctx.tape.filter("tool_result")
        assert len(tool_results) >= 1
        error_results = [
            tr for tr in tool_results if "Error" in tr.payload.get("content", "")
        ]
        assert len(error_results) >= 1

        assert outcome.final_message is not None
        assert "recovered" in outcome.final_message

    @pytest.mark.asyncio
    async def test_large_tool_result_truncated(self, tmp_path):
        """Given a tool returning 20k chars and max_tool_result_size=10000, when pipeline processes it, then tape entry is truncated with notice."""
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")

        ctx.config["max_tool_result_size"] = 10000
        large_result = "X" * 20000

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-big",
                    name="bash_run",
                    arguments={"command": "generate_large_output"},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Done processing large output.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)

        original_call_first = pipeline._runtime.call_first

        def patched_call_first(hook_name, **kwargs):
            if hook_name == "execute_tool" and kwargs.get("name") == "bash_run":
                return large_result
            return original_call_first(hook_name, **kwargs)

        pipeline._runtime.call_first = patched_call_first

        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("generate something large")

        assert isinstance(outcome, TurnOutcome)

        tool_results = ctx.tape.filter("tool_result")
        assert len(tool_results) >= 1

        content = tool_results[0].payload["content"]
        assert len(content) < 20000
        assert "truncated" in content.lower()
        assert "10000 chars truncated" in content

    @pytest.mark.asyncio
    async def test_parallel_tools_execute_concurrently(self, tmp_path):
        """Given ParallelExecutorPlugin + 2 independent tool calls in one round, when pipeline runs, then both execute and results appear in tape."""
        from coding_agent.plugins.parallel_executor import ParallelExecutorPlugin

        pipeline, ctx = _setup_agent(tmp_path)

        executed_tools: list[str] = []

        async def mock_execute_fn(name: str, arguments: dict[str, object]) -> str:
            executed_tools.append(name)
            await asyncio.sleep(0.01)
            return f"result_of_{name}({arguments})"

        parallel_plugin = ParallelExecutorPlugin(
            execute_fn=mock_execute_fn,
            max_concurrency=5,
        )
        pipeline._registry._plugins[parallel_plugin.state_key] = parallel_plugin
        for hook_name, hook_fn in parallel_plugin.hooks().items():
            pipeline._registry._hook_index[hook_name] = [
                fn
                for fn in pipeline._registry._hook_index.get(hook_name, [])
                if getattr(fn, "__self__", None).__class__ is not ParallelExecutorPlugin
            ]
            pipeline._registry._hook_index[hook_name].append(hook_fn)
        pipeline._runtime = HookRuntime(pipeline._registry)

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-p1",
                    name="file_read",
                    arguments={"path": "file_a.txt"},
                )
                yield ToolCallEvent(
                    tool_call_id="tc-p2",
                    name="grep_search",
                    arguments={"pattern": "test", "path": "."},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Both tools completed.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        adapter = PipelineAdapter(pipeline, ctx, consumer=None)
        outcome = await adapter.run_turn("read two files")

        assert isinstance(outcome, TurnOutcome)

        tool_calls = ctx.tape.filter("tool_call")
        tool_call_names = [tc.payload["name"] for tc in tool_calls]
        assert "file_read" in tool_call_names
        assert "grep_search" in tool_call_names

        tool_results = ctx.tape.filter("tool_result")
        assert len(tool_results) >= 2

    @pytest.mark.asyncio
    async def test_parallel_structured_results_preserved_in_events(self, tmp_path):
        pipeline, ctx = _setup_agent(tmp_path, approval_mode="yolo")
        ctx.config["structured_results"] = True

        core_tools = pipeline._registry.get("core_tools")
        execute_calls: list[tuple[str, dict[str, object]]] = []

        async def fake_execute_tool_async(
            name: str, arguments: dict[str, object]
        ) -> dict[str, object]:
            execute_calls.append((name, arguments))
            return {"tool": name, "arguments": arguments, "kind": "structured"}

        core_tools.execute_tool_async = fake_execute_tool_async

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc-s1",
                    name="file_read",
                    arguments={"path": "file_a.txt"},
                )
                yield ToolCallEvent(
                    tool_call_id="tc-s2",
                    name="grep_search",
                    arguments={"pattern": "test", "path": "."},
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Both tools completed.")
                yield DoneEvent()

        _mock_provider(pipeline, mock_stream)
        await pipeline.mount(ctx)

        consumer = AsyncMock()
        consumer.emit = AsyncMock()
        adapter = PipelineAdapter(pipeline, ctx, consumer=consumer)
        outcome = await adapter.run_turn("read two files")

        tool_result_deltas = [
            call.args[0]
            for call in consumer.emit.call_args_list
            if isinstance(call.args[0], ToolResultDelta)
        ]

        assert isinstance(outcome, TurnOutcome)
        assert len(tool_result_deltas) == 2
        assert all(isinstance(delta.result, dict) for delta in tool_result_deltas)
        assert {delta.result["tool"] for delta in tool_result_deltas} == {
            "file_read",
            "grep_search",
        }

        assert execute_calls == [
            ("file_read", {"path": "file_a.txt"}),
            ("grep_search", {"pattern": "test", "path": "."}),
        ]

        tool_results = ctx.tape.filter("tool_result")
        assert [json.loads(result.payload["content"]) for result in tool_results] == [
            {
                "tool": "file_read",
                "arguments": {"path": "file_a.txt"},
                "kind": "structured",
            },
            {
                "tool": "grep_search",
                "arguments": {"pattern": "test", "path": "."},
                "kind": "structured",
            },
        ]

        assert outcome.final_message is not None
        assert "completed" in outcome.final_message
