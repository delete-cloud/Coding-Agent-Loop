# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false, reportUnusedImport=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnannotatedClassAttribute=false, reportUnusedFunction=false, reportUnusedCallResult=false

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from agentkit.plugin.registry import PluginRegistry
from agentkit.runtime.pipeline import Pipeline, PipelineContext
from agentkit.runtime.hook_runtime import HookRuntime
from agentkit.runtime.hookspecs import HOOK_SPECS
from coding_agent.plugins.memory import MemoryPlugin
from coding_agent.plugins.storage import StoragePlugin
from coding_agent.plugins.topic import TopicPlugin
from agentkit.directive.types import MemoryRecord
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.tape.anchor import Anchor


class TestMemoryPlugin:
    def test_state_key(self):
        plugin = MemoryPlugin()
        assert plugin.state_key == "memory"

    def test_hooks(self):
        plugin = MemoryPlugin()
        hooks = plugin.hooks()
        assert "build_context" in hooks  # Grounding mode
        assert "on_turn_end" in hooks  # finish_action
        assert "on_session_event" in hooks
        assert "mount" in hooks

    def test_mount_returns_initial_state(self):
        plugin = MemoryPlugin()
        state = plugin.do_mount()
        assert "memories" in state
        assert isinstance(state["memories"], list)

    def test_build_context_returns_grounding_messages(self):
        plugin = MemoryPlugin()
        # Simulate having some memories
        plugin._memories = [
            {"summary": "User prefers Python", "importance": 0.9},
            {"summary": "Project uses pytest", "importance": 0.7},
        ]
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "help me debug"})
        )
        result = plugin.build_context(tape=tape)
        assert isinstance(result, list)
        assert len(result) > 0
        # Grounding messages should be system role
        assert all(msg["role"] == "system" for msg in result)

    def test_on_turn_end_returns_memory_record_directive(self):
        plugin = MemoryPlugin()
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix auth.py"})
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "I fixed the bug in auth.py"},
            )
        )
        result = plugin.on_turn_end(tape=tape)
        assert isinstance(result, MemoryRecord)
        assert result.summary != ""

    def test_on_turn_end_with_empty_tape(self):
        plugin = MemoryPlugin()
        tape = Tape()
        result = plugin.on_turn_end(tape=tape)
        # With empty tape, should return a minimal record or None
        assert result is None or isinstance(result, MemoryRecord)

    def test_memory_importance_scoring(self):
        plugin = MemoryPlugin()
        # Simple heuristic: longer conversations = more important
        tape = Tape()
        for i in range(10):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"step {i}"})
            )
            tape.append(Entry(kind="tool_call", payload={"name": "bash_run"}))
        result = plugin.on_turn_end(tape=tape)
        assert isinstance(result, MemoryRecord)
        assert result.importance > 0.3  # Multi-step should score higher


def _make_topic_initial(topic_id: str, topic_number: int = 1) -> Anchor:
    return Anchor(
        anchor_type="topic_start",
        payload={"content": f"Topic #{topic_number}"},
        meta={
            "topic_id": topic_id,
            "topic_number": topic_number,
            "prefix": "Topic Start",
        },
    )


class TestMemoryTopicScopedRecall:
    """P2: build_context filters memories by current topic's file tags."""

    def test_memories_filtered_by_topic_files(self):
        plugin = MemoryPlugin()
        plugin._memories = [
            {
                "summary": "Fixed auth bug",
                "tags": ["src/auth.py", "file_read"],
                "importance": 0.8,
            },
            {
                "summary": "Fixed UI layout",
                "tags": ["src/ui/app.tsx", "file_read"],
                "importance": 0.9,
            },
        ]
        plugin._topic_file_tags = {"src/auth.py", "src/auth_utils.py"}

        tape = Tape()
        result = plugin.build_context(tape=tape)

        assert len(result) == 1
        assert "auth" in result[0]["content"]

    def test_fallback_to_importance_when_no_topic_context(self):
        plugin = MemoryPlugin()
        plugin._memories = [
            {"summary": "Fixed auth", "tags": ["src/auth.py"], "importance": 0.8},
            {"summary": "Fixed UI", "tags": ["src/ui/app.tsx"], "importance": 0.9},
        ]
        plugin._topic_file_tags = set()

        tape = Tape()
        result = plugin.build_context(tape=tape)

        assert len(result) == 2

    def test_topic_files_updated_from_checkpoint(self):
        plugin = MemoryPlugin()
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix auth"})
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "src/auth.py"}},
            )
        )

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {
                    "topic": {"current_topic_id": "topic-abc", "topic_count": 1}
                }

        ctx = FakeCtx(tape)
        plugin.on_checkpoint(ctx=ctx)

        assert "src/auth.py" in plugin._topic_file_tags

    def test_tag_overlap_includes_partial_path_match(self):
        plugin = MemoryPlugin()
        plugin._memories = [
            {"summary": "Auth fix", "tags": ["src/auth.py"], "importance": 0.5},
            {"summary": "DB fix", "tags": ["src/db.py"], "importance": 0.5},
        ]
        plugin._topic_file_tags = {"src/auth.py", "tests/test_auth.py"}

        tape = Tape()
        result = plugin.build_context(tape=tape)

        assert len(result) == 1
        assert "Auth" in result[0]["content"]


class TestMemoryPluginDirectiveFlow:
    def test_on_turn_end_does_not_inline_append(self):
        plugin = MemoryPlugin()
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix auth.py"})
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "I fixed the bug in auth.py"},
            )
        )
        plugin.on_turn_end(tape=tape)
        assert plugin._memories == []

    def test_on_turn_end_returns_memory_record_with_correct_fields(self):
        plugin = MemoryPlugin()
        tape = Tape()
        tape.append(
            Entry(
                kind="message", payload={"role": "user", "content": "refactor auth.py"}
            )
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "auth.py"}},
            )
        )
        tape.append(
            Entry(
                kind="message",
                payload={
                    "role": "assistant",
                    "content": "Refactored auth.py successfully",
                },
            )
        )
        record = plugin.on_turn_end(tape=tape)
        assert isinstance(record, MemoryRecord)
        assert record.summary != ""
        assert isinstance(record.tags, list)
        assert 0.0 <= record.importance <= 1.0

    def test_add_memory_persists_to_memories_list(self):
        plugin = MemoryPlugin()
        record = MemoryRecord(summary="Fixed a bug", tags=["auth.py"], importance=0.8)
        plugin.add_memory(record)
        assert len(plugin._working_memories) == 1
        assert plugin._working_memories[0]["summary"] == "Fixed a bug"
        assert plugin._working_memories[0]["tags"] == ["auth.py"]
        assert plugin._working_memories[0]["importance"] == 0.8

    def test_add_memory_called_by_handler_persists(self):
        plugin = MemoryPlugin()

        async def memory_handler(directive: MemoryRecord) -> None:
            plugin.add_memory(directive)

        import asyncio

        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix something"})
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "done fixing"},
            )
        )
        record = plugin.on_turn_end(tape=tape)
        assert record is not None
        assert plugin._memories == []
        asyncio.run(memory_handler(record))
        assert plugin._memories == []
        assert len(plugin._working_memories) == 1


class TestMemoryPluginSessionEvents:
    def test_topic_end_event_adds_compacted_memory(self):
        plugin = MemoryPlugin()

        plugin.on_session_event(
            event_type="topic_end",
            payload={"topic_id": "topic-1", "files": ["src/auth.py"]},
        )

        assert len(plugin._memories) == 1
        assert plugin._memories[0]["summary"] == "Topic topic-1 completed"
        assert plugin._memories[0]["tags"] == ["src/auth.py"]

    def test_topic_end_event_uses_emitted_summary_from_topic_plugin(self):
        registry = PluginRegistry(specs=HOOK_SPECS)
        topic = TopicPlugin(overlap_threshold=0.2, min_entries_before_detect=2)
        memory = MemoryPlugin()
        registry.register(topic)
        registry.register(memory)
        runtime = HookRuntime(registry, specs=HOOK_SPECS)

        tape = Tape()

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {}

        ctx = FakeCtx(tape)

        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix auth"})
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "src/auth.py"}},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "done"})
        )
        topic.on_checkpoint(ctx=ctx, runtime=runtime)

        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix ui"})
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "src/ui/app.tsx"}},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "done"})
        )
        topic.on_checkpoint(ctx=ctx, runtime=runtime)

        assert len(memory._memories) == 1
        assert memory._memories[0]["summary"] == "Topic involved files: src/auth.py"
        assert memory._memories[0]["tags"] == ["src/auth.py"]


class TestMemoryPersistence:
    @pytest.mark.asyncio
    async def test_mount_loads_persisted_memory_records_with_importance_decay(
        self, tmp_path: Path
    ):
        storage = StoragePlugin(data_dir=tmp_path)
        tape_store = storage._get_jsonl_store()
        tape_store.append_memory_record(
            "session-1",
            {"summary": "Persisted memory", "tags": ["src/auth.py"], "importance": 1.0},
        )

        registry = PluginRegistry(specs=HOOK_SPECS)
        registry.register(storage)
        memory = MemoryPlugin()
        registry.register(memory)
        runtime = HookRuntime(registry, specs=HOOK_SPECS)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        ctx = PipelineContext(tape=Tape(), session_id="session-1", config={})

        await pipeline.mount(ctx)

        assert memory._memories == [
            {
                "summary": "Persisted memory",
                "tags": ["src/auth.py"],
                "importance": 0.9,
            }
        ]

    @pytest.mark.asyncio
    async def test_topic_end_compacts_working_memory_into_persistent_record(
        self, tmp_path: Path
    ):
        storage = StoragePlugin(data_dir=tmp_path)
        registry = PluginRegistry(specs=HOOK_SPECS)
        registry.register(storage)
        memory = MemoryPlugin()
        registry.register(memory)
        runtime = HookRuntime(registry, specs=HOOK_SPECS)
        pipeline = Pipeline(runtime=runtime, registry=registry)
        ctx = PipelineContext(tape=Tape(), session_id="session-2", config={})

        await pipeline.mount(ctx)

        memory.add_memory(
            MemoryRecord(summary="Step 1", tags=["src/auth.py"], importance=0.7)
        )
        memory.add_memory(
            MemoryRecord(summary="Step 2", tags=["tests/test_auth.py"], importance=0.9)
        )

        memory.on_session_event(
            event_type="topic_end",
            payload={
                "topic_id": "topic-1",
                "summary": "Topic involved files: src/auth.py",
                "files": ["src/auth.py"],
            },
        )

        assert memory._working_memories == []
        assert memory._memories == [
            {
                "summary": "Topic involved files: src/auth.py",
                "tags": ["src/auth.py", "tests/test_auth.py"],
                "importance": 0.8,
            }
        ]

        reloaded_memory = MemoryPlugin()
        reload_registry = PluginRegistry(specs=HOOK_SPECS)
        reload_registry.register(storage)
        reload_registry.register(reloaded_memory)
        reload_runtime = HookRuntime(reload_registry, specs=HOOK_SPECS)
        reload_pipeline = Pipeline(runtime=reload_runtime, registry=reload_registry)
        reload_ctx = PipelineContext(tape=Tape(), session_id="session-2", config={})

        await reload_pipeline.mount(reload_ctx)

        assert reloaded_memory._memories == [
            {
                "summary": "Topic involved files: src/auth.py",
                "tags": ["src/auth.py", "tests/test_auth.py"],
                "importance": 0.72,
            }
        ]
