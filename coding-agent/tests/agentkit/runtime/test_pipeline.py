import pytest
from unittest.mock import AsyncMock, MagicMock
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.errors import PipelineError


class MinimalPlugin:
    state_key = "minimal"

    def __init__(self):
        self.mounted = False
        self.mount_called = False
        self._mock_llm = MagicMock()

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
        self.mount_called = True
        return {"ready": True}

    def provide_llm(self, **kwargs):
        return self._mock_llm

    def provide_storage(self, **kwargs):
        return MagicMock()

    def get_tools(self, **kwargs):
        return []

    def build_context(self, **kwargs):
        return []

    def summarize_context(self, **kwargs):
        return None

    def execute_tool(self, name: str = "", **kwargs):
        return f"executed:{name}"


class TestPipelineContext:
    def test_create_context(self):
        tape = Tape()
        ctx = PipelineContext(
            tape=tape,
            session_id="ses-1",
            config={"model": "gpt-4"},
        )
        assert ctx.tape is tape
        assert ctx.session_id == "ses-1"
        assert ctx.config["model"] == "gpt-4"
        assert ctx.plugin_states == {}

    def test_context_plugin_state_access(self):
        ctx = PipelineContext(tape=Tape(), session_id="x")
        ctx.plugin_states["memory"] = {"last_query": "test"}
        assert ctx.plugin_states["memory"]["last_query"] == "test"


class TestPipeline:
    @pytest.fixture
    def setup(self):
        registry = PluginRegistry()
        plugin = MinimalPlugin()
        registry.register(plugin)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        return pipeline, plugin

    def test_pipeline_creates(self, setup):
        pipeline, _ = setup
        assert pipeline is not None

    @pytest.mark.asyncio
    async def test_mount_calls_plugins(self, setup):
        pipeline, plugin = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")
        await pipeline.mount(ctx)
        assert plugin.mount_called

    @pytest.mark.asyncio
    async def test_mount_populates_plugin_states(self, setup):
        pipeline, _ = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")
        await pipeline.mount(ctx)
        assert "minimal" in ctx.plugin_states

    def test_pipeline_stages_defined(self, setup):
        pipeline, _ = setup
        stages = pipeline.stage_names
        expected = [
            "resolve_session",
            "load_state",
            "build_context",
            "run_model",
            "save_state",
            "render",
            "dispatch",
        ]
        assert stages == expected

    @pytest.mark.asyncio
    async def test_run_single_turn(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import TextEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        result = await pipeline.run_turn(ctx)
        assert result is not None
        last_entry = list(ctx.tape)[-1]
        assert last_entry.payload["role"] == "assistant"
        assert "Hello back!" in last_entry.payload["content"]

    @pytest.mark.asyncio
    async def test_run_turn_with_tool_call(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "read file.txt"})
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
                    tool_call_id="tc1", name="file_read", arguments={"path": "file.txt"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="File contents: test data")
                yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        result = await pipeline.run_turn(ctx)
        entries = list(ctx.tape)
        assert any(e.kind == "tool_call" for e in entries)
        assert any(e.kind == "tool_result" for e in entries)
        assert entries[-1].payload["role"] == "assistant"
