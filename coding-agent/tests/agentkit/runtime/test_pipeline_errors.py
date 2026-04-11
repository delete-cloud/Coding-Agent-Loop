"""Tests for Pipeline tool execution error handling."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class ErrorPlugin:
    """Plugin with a tool that raises RuntimeError."""

    state_key = "error_plugin"

    def __init__(self):
        self.mounted = False
        self.mount_called = False
        self._mock_llm = MagicMock()
        self._mock_storage = object()

    def hooks(self):
        return {
            "mount": self.do_mount,
            "provide_llm": self.provide_llm,
            "provide_storage": self.provide_storage,
            "get_tools": self.get_tools,
            "build_context": self.build_context,
            "execute_tool": self.execute_tool,
        }

    def do_mount(self, **kwargs):
        self.mount_called = True
        return {"ready": True}

    def provide_llm(self, **kwargs):
        return self._mock_llm

    def provide_storage(self, **kwargs):
        return self._mock_storage

    def get_tools(self, **kwargs):
        return []

    def build_context(self, **kwargs):
        return []

    def execute_tool(self, name: str = "", **kwargs):
        """Mock tool that raises RuntimeError."""
        if name == "failing_tool":
            raise RuntimeError(f"Tool '{name}' failed: simulated error")
        return f"executed:{name}"


class TestPipelineToolErrorHandling:
    @pytest.fixture
    def setup(self):
        registry = PluginRegistry()
        plugin = ErrorPlugin()
        registry.register(plugin)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        return pipeline, plugin

    @pytest.mark.asyncio
    async def test_tool_error_recorded_in_tape(self, setup):
        """Verify that tool RuntimeError is caught and recorded in tape as tool_result."""
        pipeline, plugin = setup
        tape = Tape()
        tape.append(
            Entry(
                kind="message", payload={"role": "user", "content": "run failing tool"}
            )
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First turn: LLM calls the failing tool
                yield ToolCallEvent(
                    tool_call_id="tc1", name="failing_tool", arguments={}
                )
                yield DoneEvent()
            else:
                # Second turn: LLM responds after seeing error
                yield TextEvent(text="I see the tool failed")
                yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        # This should NOT crash; error should be recorded in tape
        result = await pipeline.run_turn(ctx)
        assert result is not None

        # Verify error was recorded in tape
        tool_result_entries = [e for e in ctx.tape if e.kind == "tool_result"]
        assert len(tool_result_entries) >= 1

        # Find the tool_result for tc1
        tc1_result = None
        for entry in tool_result_entries:
            if entry.payload.get("tool_call_id") == "tc1":
                tc1_result = entry
                break

        assert tc1_result is not None, "Expected tool_result entry for tc1"
        # Verify error message is in the content
        assert "failing_tool" in tc1_result.payload["content"]
        assert "failed" in tc1_result.payload["content"].lower()

    @pytest.mark.asyncio
    async def test_pipeline_continues_after_error(self, setup):
        """Verify Pipeline continues execution after tool error."""
        pipeline, plugin = setup
        tape = Tape()
        tape.append(
            Entry(
                kind="message", payload={"role": "user", "content": "run failing tool"}
            )
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="failing_tool", arguments={}
                )
                yield DoneEvent()
            else:
                # After error, LLM should be able to respond
                yield TextEvent(text="Tool failed, but I'll help anyway")
                yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        # Pipeline should complete successfully despite tool error
        result = await pipeline.run_turn(ctx)
        assert result is not None

        # Final message should be from assistant
        entries = list(ctx.tape)
        assert len(entries) > 1
        last_entry = entries[-1]
        assert last_entry.payload.get("role") == "assistant"
        assert "Tool failed" in last_entry.payload["content"]

    @pytest.mark.asyncio
    async def test_successful_tool_still_works(self, setup):
        """Verify successful tool calls are unaffected by error handling."""
        pipeline, plugin = setup
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "run ok"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        call_count = 0

        async def mock_stream(messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCallEvent(
                    tool_call_id="tc1", name="ok_tool", arguments={"param": "value"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="Tool succeeded with result")
                yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)

        # Verify successful tool result is recorded
        tool_result_entries = [e for e in ctx.tape if e.kind == "tool_result"]
        assert len(tool_result_entries) >= 1

        tc1_result = None
        for entry in tool_result_entries:
            if entry.payload.get("tool_call_id") == "tc1":
                tc1_result = entry
                break

        assert tc1_result is not None
        assert "executed:ok_tool" in tc1_result.payload["content"]
