import pytest
from unittest.mock import AsyncMock, MagicMock
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.plugin.registry import PluginRegistry
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.tape.anchor import Anchor
from agentkit.errors import PipelineError
from agentkit.tools.schema import ToolSchema


class MinimalPlugin:
    state_key = "minimal"

    def __init__(self):
        self.mounted = False
        self.mount_called = False
        self.shutdown_called = False
        self._mock_llm = MagicMock()
        self._mock_storage = object()
        self._summary_result = None

    def hooks(self):
        return {
            "mount": self.do_mount,
            "on_shutdown": self.on_shutdown,
            "provide_llm": self.provide_llm,
            "provide_storage": self.provide_storage,
            "get_tools": self.get_tools,
            "build_context": self.build_context,
            "summarize_context": self.summarize_context,
        }

    def do_mount(self, **kwargs):
        self.mount_called = True
        return {"ready": True}

    def on_shutdown(self, **kwargs):
        self.shutdown_called = True

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


class GreedyToolPlugin:
    state_key = "greedy_tool"

    def hooks(self):
        return {
            "execute_tool": self.execute_tool,
        }

    def execute_tool(self, name: str = "", **kwargs):
        if name != "known_tool":
            return None
        return "known-tool-result"


class SkillsLikePlugin:
    state_key = "skills_like"

    def hooks(self):
        return {
            "get_tools": self.get_tools,
            "execute_tool": self.execute_tool,
        }

    def get_tools(self, **kwargs):
        return [
            ToolSchema(
                name="skill_invoke",
                description="Activate a skill",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            )
        ]

    def execute_tool(
        self, name: str = "", arguments: dict[str, object] | None = None, **kwargs
    ):
        if name != "skill_invoke":
            return None
        return f"activated:{(arguments or {}).get('name', '')}"


class BatchToolPlugin:
    state_key = "batch_tool"

    def __init__(self, batch_results):
        self.batch_results = batch_results

    def hooks(self):
        return {
            "execute_tools_batch": self.execute_tools_batch,
        }

    def execute_tools_batch(self, tool_calls=None, **kwargs):
        del tool_calls, kwargs
        return self.batch_results


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

    @pytest.mark.asyncio
    async def test_shutdown_notifies_plugins(self, setup):
        pipeline, plugin = setup
        ctx = PipelineContext(tape=Tape(), session_id="s1")

        await pipeline.shutdown(ctx)

        assert plugin.shutdown_called is True

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
    async def test_run_turn_allows_later_plugin_to_handle_unknown_tool(self):
        registry = PluginRegistry()
        llm_plugin = MinimalPlugin()
        greedy_tool = GreedyToolPlugin()
        skills = SkillsLikePlugin()
        registry.register(llm_plugin)
        registry.register(greedy_tool)
        registry.register(skills)
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        async def mock_stream(messages, tools=None, **kwargs):
            from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent

            if not any(entry.kind == "tool_result" for entry in ctx.tape):
                assert tools is not None
                yield ToolCallEvent(
                    tool_call_id="tc-skill-1",
                    name="skill_invoke",
                    arguments={"name": "using-superpowers"},
                )
                yield DoneEvent()
                return

            yield TextEvent(text="Skill activated")
            yield DoneEvent()

        mock_llm = MagicMock()
        mock_llm.stream = mock_stream
        llm_plugin._mock_llm = mock_llm

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        ctx = PipelineContext(tape=tape, session_id="s-skill")
        await pipeline.mount(ctx)

        result = await pipeline.run_turn(ctx)

        tool_result_entries = [e for e in ctx.tape if e.kind == "tool_result"]
        assert tool_result_entries
        assert (
            tool_result_entries[0].payload["content"] == "activated:using-superpowers"
        )
        assert result is not None

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
                anchor = Anchor(
                    anchor_type="handoff",
                    payload={"content": "summary"},
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
    async def test_build_context_can_advance_window_multiple_times_in_same_turn(
        self, setup
    ):
        pipeline, plugin = setup
        tape = Tape()
        for i in range(12):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        ctx = PipelineContext(tape=tape, session_id="s1")

        class WindowPlugin:
            state_key = "window-multi"

            def hooks(self):
                return {"resolve_context_window": self.resolve_context_window}

            def resolve_context_window(self, tape=None, **kwargs):
                if tape is None:
                    return None
                visible = tape.windowed_entries()
                if len(visible) > 6:
                    return (
                        len(visible) - 5,
                        Anchor(anchor_type="handoff", payload={"content": "summary"}),
                    )
                return None

        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(WindowPlugin())
        pipeline2 = Pipeline(runtime=HookRuntime(registry), registry=registry)

        await pipeline2._stage_build_context(ctx)
        first_window_start = ctx.tape.window_start

        ctx.tape.append(
            Entry(kind="message", payload={"role": "user", "content": "after-1"})
        )
        ctx.tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "after-2"})
        )
        await pipeline2._stage_build_context(ctx)

        anchors = [entry for entry in ctx.tape if entry.kind == "anchor"]
        assert len(anchors) == 2
        assert ctx.tape.window_start > first_window_start

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

    @pytest.mark.asyncio
    async def test_run_turn_raises_when_batch_results_are_too_few(self, setup):
        pipeline, plugin = setup
        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(BatchToolPlugin(["only-one-result"]))
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "do two things"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="tc1", name="file_read", arguments={"path": "a.txt"}
            )
            yield ToolCallEvent(
                tool_call_id="tc2", name="file_read", arguments={"path": "b.txt"}
            )
            yield DoneEvent()

        plugin._mock_llm = MagicMock()
        plugin._mock_llm.stream = mock_stream

        with pytest.raises(
            PipelineError,
            match="execute_tools_batch returned 1 results for 2 tool calls",
        ):
            await pipeline.run_turn(ctx)

    @pytest.mark.asyncio
    async def test_run_turn_raises_when_batch_results_are_too_many(self, setup):
        pipeline, plugin = setup
        registry = PluginRegistry()
        registry.register(plugin)
        registry.register(BatchToolPlugin(["r1", "r2", "r3"]))
        pipeline = Pipeline(runtime=HookRuntime(registry), registry=registry)

        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "do two things"})
        )
        ctx = PipelineContext(tape=tape, session_id="s1")
        await pipeline.mount(ctx)

        from agentkit.providers.models import ToolCallEvent, DoneEvent

        async def mock_stream(messages, tools=None, **kwargs):
            yield ToolCallEvent(
                tool_call_id="tc1", name="file_read", arguments={"path": "a.txt"}
            )
            yield ToolCallEvent(
                tool_call_id="tc2", name="file_read", arguments={"path": "b.txt"}
            )
            yield DoneEvent()

        plugin._mock_llm = MagicMock()
        plugin._mock_llm.stream = mock_stream

        with pytest.raises(
            PipelineError,
            match="execute_tools_batch returned 3 results for 2 tool calls",
        ):
            await pipeline.run_turn(ctx)


class TestPipelineView:
    @pytest.mark.asyncio
    async def test_build_context_uses_view(self):
        from agentkit.tape.view import TapeView

        registry = PluginRegistry()

        class ViewTestPlugin:
            state_key = "view_test"

            def hooks(self):
                return {
                    "provide_llm": lambda **kw: None,
                    "provide_storage": lambda **kw: None,
                    "get_tools": lambda **kw: [],
                    "build_context": lambda **kw: [],
                }

        registry.register(ViewTestPlugin())
        runtime = HookRuntime(registry)
        pipeline = Pipeline(runtime=runtime, registry=registry)

        tape = Tape()
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"old-{i}"})
            )
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary"},
            meta={"is_handoff": True, "prefix": "Summary"},
        )
        tape.handoff(anchor)
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "new msg"})
        )

        ctx = PipelineContext(
            tape=tape,
            session_id="s1",
            config={"system_prompt": "test"},
        )
        await pipeline.mount(ctx)
        await pipeline._stage_build_context(ctx)

        assert len(ctx.messages) == 3
        assert "[Summary]" in ctx.messages[1]["content"]
        assert ctx.messages[2]["content"] == "new msg"
        assert not any("old-" in str(m.get("content", "")) for m in ctx.messages)
