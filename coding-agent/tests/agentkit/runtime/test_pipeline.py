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
        self._mock_storage = object()
        self._summary_result = None

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
        return self._mock_storage

    def get_tools(self, **kwargs):
        return []

    def build_context(self, **kwargs):
        return []

    def summarize_context(self, **kwargs):
        return self._summary_result

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

    @pytest.mark.asyncio
    async def test_run_turn_commits_active_fork_and_updates_context_tape(self, setup):
        pipeline, plugin = setup

        class RecordingStorage:
            def __init__(self):
                self.begin_calls = []
                self.commit_calls = []
                self.rollback_calls = []

            def begin(self, tape):
                self.begin_calls.append(tape)
                return tape.fork()

            async def commit(self, tape):
                self.commit_calls.append(tape)

            def rollback(self, tape):
                self.rollback_calls.append(tape)

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        storage = RecordingStorage()
        plugin._mock_storage = storage

        from agentkit.providers.models import TextEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield TextEvent(text="Hello back!")
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        original_tape = ctx.tape
        await pipeline.run_turn(ctx)

        assert storage.begin_calls == [original_tape]
        assert len(storage.commit_calls) == 1
        assert storage.rollback_calls == []
        assert ctx.tape is storage.commit_calls[0]
        assert ctx.tape is not original_tape
        assert ctx.tape.parent_id == original_tape.tape_id

    @pytest.mark.asyncio
    async def test_build_context_applies_summary_entries(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "older"}))
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "latest"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")

        summary_entries = [
            Entry(kind="anchor", payload={"content": "summary"}),
            Entry(kind="message", payload={"role": "user", "content": "latest"}),
        ]
        plugin._summary_result = summary_entries

        await pipeline._stage_build_context(ctx)

        entries = list(ctx.tape)
        assert len(entries) == 2
        assert entries[0].kind == "anchor"
        assert entries[0].payload["content"] == "summary"
        assert ctx.messages[1]["role"] == "system"
        assert ctx.messages[1]["content"] == "summary"

    @pytest.mark.asyncio
    async def test_build_context_uses_windowing(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                if tape is None:
                    return None
                anchor = Entry(
                    kind="anchor",
                    payload={"content": "summary of old entries"},
                    meta={"is_handoff": True, "source_entry_count": 7},
                )
                return (7, anchor)

        window_plugin = WindowPlugin()
        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(window_plugin)
        runtime = HookRuntime(registry)
        pipeline2 = Pipeline(runtime=runtime, registry=registry)

        await pipeline2._stage_build_context(ctx)

        # All original entries preserved + the anchor
        assert len(ctx.tape) == 11  # 10 original + 1 anchor
        # The anchor is in the tape
        anchors = [e for e in ctx.tape if e.kind == "anchor"]
        assert len(anchors) == 1
        assert anchors[0].meta.get("is_handoff")

    @pytest.mark.asyncio
    async def test_build_context_passes_window_start_to_handoff(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window2"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                anchor = Entry(
                    kind="anchor",
                    payload={"content": "summary"},
                    meta={"is_handoff": True},
                )
                return (7, anchor)

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(WindowPlugin())
        pipeline2 = Pipeline(runtime=HookRuntime(registry), registry=registry)

        await pipeline2._stage_build_context(ctx)

        windowed = ctx.tape.windowed_entries()
        assert len(windowed) == 4  # entries[7:10] + anchor
        assert windowed[-1].kind == "anchor"

    @pytest.mark.asyncio
    async def test_build_context_reentrant_does_not_double_handoff(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window3"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                anchor = Entry(
                    kind="anchor",
                    payload={"content": "summary"},
                    meta={"is_handoff": True},
                )
                return (7, anchor)

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(WindowPlugin())
        pipeline2 = Pipeline(runtime=HookRuntime(registry), registry=registry)

        await pipeline2._stage_build_context(ctx)
        await pipeline2._stage_build_context(ctx)

        anchors = [e for e in ctx.tape if e.kind == "anchor"]
        assert len(anchors) == 1

    @pytest.mark.asyncio
    async def test_run_turn_records_one_tool_call_entry_per_call(self, setup):
        pipeline, plugin = setup
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "do two things"})
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
                    tool_call_id="tc1", name="file_read", arguments={"path": "a.txt"}
                )
                yield ToolCallEvent(
                    tool_call_id="tc2", name="file_read", arguments={"path": "b.txt"}
                )
                yield DoneEvent()
            else:
                yield TextEvent(text="done")
                yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        plugin._mock_llm = mock_llm

        await pipeline.run_turn(ctx)

        tool_call_entries = [e for e in ctx.tape if e.kind == "tool_call"]
        assert len(tool_call_entries) == 2
        assert tool_call_entries[0].payload == {
            "id": "tc1",
            "name": "file_read",
            "arguments": {"path": "a.txt"},
            "role": "assistant",
        }
        assert tool_call_entries[1].payload == {
            "id": "tc2",
            "name": "file_read",
            "arguments": {"path": "b.txt"},
            "role": "assistant",
        }
