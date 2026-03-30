import pytest
from coding_agent.plugins.topic import TopicPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestTopicPlugin:
    def test_state_key(self):
        plugin = TopicPlugin()
        assert plugin.state_key == "topic"

    def test_hooks_registered(self):
        plugin = TopicPlugin()
        hooks = plugin.hooks()
        assert "on_checkpoint" in hooks
        assert "on_session_event" in hooks

    def test_mount_returns_initial_state(self):
        plugin = TopicPlugin()
        state = plugin.do_mount()
        assert "current_topic_id" in state
        assert state["current_topic_id"] is None
        assert "topic_count" in state
        assert state["topic_count"] == 0

    def test_first_turn_creates_initial_topic(self):
        plugin = TopicPlugin()
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix auth.py"})
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "src/auth.py"}},
            )
        )
        tape.append(
            Entry(
                kind="tool_result",
                payload={"tool_call_id": "tc1", "content": "file contents"},
            )
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "I see the issue"},
            )
        )

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {}

        ctx = FakeCtx(tape)
        plugin.on_checkpoint(ctx=ctx)

        assert plugin.current_topic_id is not None
        assert plugin.topic_count == 1

        anchors = tape.filter("anchor")
        assert len(anchors) == 1
        assert anchors[0].meta.get("anchor_type") == "topic_initial"

    def test_topic_switch_on_file_path_change(self):
        plugin = TopicPlugin(overlap_threshold=0.2, min_entries_before_detect=2)
        tape = Tape()

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {}

        # Turn 1: auth files
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
            Entry(
                kind="tool_call",
                payload={
                    "name": "file_read",
                    "arguments": {"path": "src/auth_utils.py"},
                },
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "done"})
        )
        ctx = FakeCtx(tape)
        plugin.on_checkpoint(ctx=ctx)
        first_topic_id = plugin.current_topic_id
        assert first_topic_id is not None
        assert plugin.topic_count == 1

        # Turn 2: completely different files → topic switch
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "now fix the UI"})
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "name": "file_read",
                    "arguments": {"path": "src/ui/dashboard.tsx"},
                },
            )
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "name": "file_read",
                    "arguments": {"path": "src/ui/sidebar.tsx"},
                },
            )
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "looking at UI"},
            )
        )
        plugin.on_checkpoint(ctx=ctx)

        assert plugin.topic_count == 2
        assert plugin.current_topic_id != first_topic_id

        anchors = tape.filter("anchor")
        assert len(anchors) == 3
        types = [a.meta.get("anchor_type") for a in anchors]
        assert types == ["topic_initial", "topic_finalized", "topic_initial"]

    def test_no_switch_when_files_overlap(self):
        plugin = TopicPlugin(overlap_threshold=0.2, min_entries_before_detect=2)
        tape = Tape()

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {}

        ctx = FakeCtx(tape)
        # Turn 1
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
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "src/utils.py"}},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "found it"})
        )
        plugin.on_checkpoint(ctx=ctx)
        assert plugin.topic_count == 1

        # Turn 2: still auth-related (auth.py overlaps)
        tape.append(
            Entry(
                kind="message",
                payload={"role": "user", "content": "now fix auth tests"},
            )
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "src/auth.py"}},
            )
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "name": "file_read",
                    "arguments": {"path": "tests/test_auth.py"},
                },
            )
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "tests updated"},
            )
        )
        plugin.on_checkpoint(ctx=ctx)

        assert plugin.topic_count == 1
        anchors = tape.filter("anchor")
        assert len(anchors) == 1  # only the initial topic_initial

    def test_extract_files_from_multiple_arg_keys(self):
        plugin = TopicPlugin()
        entries = [
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "a.py"}},
            ),
            Entry(
                kind="tool_call",
                payload={"name": "edit_file", "arguments": {"file": "b.py"}},
            ),
            Entry(
                kind="tool_call",
                payload={"name": "bash_run", "arguments": {"cmd": "ls"}},
            ),
        ]
        files = plugin._extract_files_from_recent(entries)
        assert "a.py" in files
        assert "b.py" in files
        assert len(files) == 2  # bash_run has no file path

    def test_no_topic_change_with_no_tool_calls(self):
        plugin = TopicPlugin(min_entries_before_detect=2)
        tape = Tape()

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {}

        ctx = FakeCtx(tape)
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "hi"})
        )
        plugin.on_checkpoint(ctx=ctx)
        assert plugin.current_topic_id is not None
        assert plugin.topic_count == 1
        assert ctx.plugin_states["topic"]["topic_count"] == 1

        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "what is 2+2?"})
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "4"})
        )
        plugin.on_checkpoint(ctx=ctx)
        # No file paths → no overlap check → no switch
        assert plugin.topic_count == 1
        assert ctx.plugin_states["topic"]["topic_count"] == 1

    def test_emits_session_events_on_topic_start_and_end(self):
        class FakeRuntime:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict]] = []

            def notify(self, hook_name: str, **kwargs):
                if hook_name == "on_session_event":
                    self.events.append(
                        (kwargs.get("event_type", ""), kwargs.get("payload") or {})
                    )

        plugin = TopicPlugin(overlap_threshold=0.2, min_entries_before_detect=2)
        tape = Tape()

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {}

        ctx = FakeCtx(tape)
        runtime = FakeRuntime()

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
        plugin.on_checkpoint(ctx=ctx, runtime=runtime)
        assert any(t == "topic_start" for t, _ in runtime.events)

        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "now fix ui"})
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"name": "file_read", "arguments": {"path": "src/ui/app.tsx"}},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "ok"})
        )
        plugin.on_checkpoint(ctx=ctx, runtime=runtime)
        types = [t for t, _ in runtime.events]
        assert "topic_end" in types
        assert types.count("topic_start") == 2
