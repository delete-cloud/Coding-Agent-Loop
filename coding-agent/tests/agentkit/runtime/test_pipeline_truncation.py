"""Tests for tool result truncation in Pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TruncationTestPlugin:
    """Plugin for testing tool result truncation."""

    state_key = "truncation_test"

    def __init__(self):
        self.mounted = False
        self._mock_llm = MagicMock()
        self._mock_storage = object()
        self._tool_result = None

    def hooks(self):
        return {
            "mount": self.do_mount,
            "provide_llm": self.provide_llm,
            "provide_storage": self.provide_storage,
            "get_tools": self.get_tools,
            "build_context": self.build_context,
            "summarize_context": self.summarize_context,
            "execute_tool": self.execute_tool,
        }

    def do_mount(self, **kwargs):
        self.mounted = True
        return {"ready": True}

    def provide_llm(self, **kwargs):
        return self._mock_llm

    def provide_storage(self, **kwargs):
        return self._mock_storage

    def get_tools(self, **kwargs):
        return []

    def build_context(self, **kwargs):
        return []

    def summarize_context(self, **kwargs):
        return None

    def execute_tool(self, name: str = "", **kwargs):
        return self._tool_result


@pytest.fixture
def truncation_setup():
    """Setup for truncation tests."""
    registry = PluginRegistry()
    plugin = TruncationTestPlugin()
    registry.register(plugin)
    runtime = HookRuntime(registry)
    pipeline = Pipeline(runtime=runtime, registry=registry)
    return pipeline, plugin


class TestToolResultTruncation:
    """Test suite for tool result truncation feature."""

    @pytest.mark.asyncio
    async def test_large_result_truncated(self, truncation_setup):
        """Test that large tool results are truncated to configured max size."""
        pipeline, plugin = truncation_setup

        # Create a 1MB string
        large_result = "x" * (1024 * 1024)
        plugin._tool_result = large_result

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "test"}))

        # Configure max_tool_result_size to 10000 (default)
        ctx = PipelineContext(
            tape=tape,
            session_id="s1",
            config={"max_tool_result_size": 10000},
        )
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="call_1",
                name="test_tool",
                arguments={"arg": "value"},
            )
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)

        # Find the tool result entry in tape
        entries = list(ctx.tape)
        tool_result_entry = None
        for entry in entries:
            if entry.kind == "tool_result":
                tool_result_entry = entry
                break

        assert tool_result_entry is not None, "No tool_result entry found"

        content = tool_result_entry.payload["content"]

        # Check that content is truncated
        assert len(content) < len(large_result), "Content should be truncated"
        assert len(content) <= 10000 + len("\n... (1048576 chars truncated)"), (
            "Truncated content should not exceed max_tool_result_size + suffix"
        )

        # Check for truncation suffix
        assert "... (" in content, "Truncation suffix should be present"
        assert "chars truncated)" in content, (
            "Truncation message should indicate chars truncated"
        )
        assert "1038576" in content, (
            "Truncation message should show original length - max_size (1048576 - 10000)"
        )

    @pytest.mark.asyncio
    async def test_small_result_not_truncated(self, truncation_setup):
        """Test that small tool results are NOT truncated."""
        pipeline, plugin = truncation_setup

        # Create a small result
        small_result = "Hello from tool"
        plugin._tool_result = small_result

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "test"}))

        ctx = PipelineContext(
            tape=tape,
            session_id="s1",
            config={"max_tool_result_size": 10000},
        )
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="call_1",
                name="test_tool",
                arguments={"arg": "value"},
            )
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)

        # Find the tool result entry in tape
        entries = list(ctx.tape)
        tool_result_entry = None
        for entry in entries:
            if entry.kind == "tool_result":
                tool_result_entry = entry
                break

        assert tool_result_entry is not None, "No tool_result entry found"

        content = tool_result_entry.payload["content"]

        # Check that content is NOT truncated
        assert content == small_result, (
            f"Small result should not be truncated. Got: {content}"
        )
        assert "..." not in content, "Small result should not have truncation suffix"

    @pytest.mark.asyncio
    async def test_custom_max_tool_result_size(self, truncation_setup):
        """Test that custom max_tool_result_size is respected."""
        pipeline, plugin = truncation_setup

        # Create a 5000 char result
        result = "x" * 5000
        plugin._tool_result = result

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "test"}))

        # Set custom max size to 1000
        ctx = PipelineContext(
            tape=tape,
            session_id="s1",
            config={"max_tool_result_size": 1000},
        )
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="call_1",
                name="test_tool",
                arguments={"arg": "value"},
            )
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)

        # Find the tool result entry in tape
        entries = list(ctx.tape)
        tool_result_entry = None
        for entry in entries:
            if entry.kind == "tool_result":
                tool_result_entry = entry
                break

        assert tool_result_entry is not None, "No tool_result entry found"

        content = tool_result_entry.payload["content"]

        # Check that content is truncated to custom max size
        assert len(content) < len(result), "Content should be truncated"
        assert (
            "1000 x's followed by suffix" not in content
        )  # It should be truncated at 1000
        assert "4000 chars truncated" in content, (
            "Should show 5000 - 1000 = 4000 chars truncated"
        )

    @pytest.mark.asyncio
    async def test_default_max_tool_result_size(self, truncation_setup):
        """Test that default max_tool_result_size is 10000."""
        pipeline, plugin = truncation_setup

        # Create a result slightly larger than 10000
        result = "x" * 10500
        plugin._tool_result = result

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "test"}))

        # Don't specify max_tool_result_size, should default to 10000
        ctx = PipelineContext(
            tape=tape,
            session_id="s1",
            config={},
        )
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="call_1",
                name="test_tool",
                arguments={"arg": "value"},
            )
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)

        # Find the tool result entry in tape
        entries = list(ctx.tape)
        tool_result_entry = None
        for entry in entries:
            if entry.kind == "tool_result":
                tool_result_entry = entry
                break

        assert tool_result_entry is not None, "No tool_result entry found"

        content = tool_result_entry.payload["content"]

        # Check that content is truncated using default size
        assert len(content) < len(result), "Content should be truncated"
        assert "500 chars truncated" in content, (
            "Should use default 10000 and show 10500 - 10000 = 500 chars truncated"
        )
