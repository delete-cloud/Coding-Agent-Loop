import pytest
from coding_agent.plugins.summarizer import SummarizerPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.tape.anchor import Anchor


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


def _make_topic_finalized(topic_id: str, files: list[str] | None = None) -> Anchor:
    return Anchor(
        anchor_type="topic_end",
        payload={"content": f"Topic involved files: {', '.join(files or [])}"},
        meta={
            "topic_id": topic_id,
            "files": files or [],
        },
    )


class TestSummarizerPlugin:
    def test_state_key(self):
        plugin = SummarizerPlugin(max_entries=10)
        assert plugin.state_key == "summarizer"

    def test_hooks_include_resolve_context_window(self):
        plugin = SummarizerPlugin(max_entries=10)
        hooks = plugin.hooks()
        assert "resolve_context_window" in hooks

    def test_short_tape_unchanged(self):
        plugin = SummarizerPlugin(max_entries=100)
        tape = Tape()
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg {i}"})
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is None

    def test_long_tape_gets_summarized(self):
        plugin = SummarizerPlugin(max_entries=5)
        tape = Tape()
        for i in range(20):
            tape.append(
                Entry(
                    kind="message",
                    payload={"role": "user", "content": f"message number {i}"},
                )
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert isinstance(split_point, int)
        assert isinstance(anchor, Anchor)
        assert anchor.is_handoff is True
        assert len(anchor.source_ids) == 2

    def test_preserves_recent_entries(self):
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        for i in range(20):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert split_point == len(list(tape)) - 3

    def test_legacy_summarize_context_still_works(self):
        plugin = SummarizerPlugin(max_entries=5)
        tape = Tape()
        for i in range(20):
            tape.append(
                Entry(
                    kind="message",
                    payload={"role": "user", "content": f"message number {i}"},
                )
            )
        result = plugin.summarize_context(tape=tape)
        assert result is not None
        assert len(result) < 20

    def test_legacy_summarize_context_includes_source_ids(self):
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        entries = []
        for i in range(20):
            e = Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            entries.append(e)
            tape.append(e)
        result = plugin.summarize_context(tape=tape)
        assert result is not None
        anchor = result[0]
        assert isinstance(anchor, Anchor)
        assert len(anchor.source_ids) == 2
        assert anchor.source_ids == (entries[0].id, entries[16].id)

    def test_summary_anchor_has_source_ids(self):
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        entries = []
        for i in range(20):
            e = Entry(
                kind="message",
                payload={"role": "user", "content": f"msg-{i}"},
            )
            entries.append(e)
            tape.append(e)
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        _, anchor = result
        assert isinstance(anchor, Anchor)
        assert anchor.source_ids == (entries[0].id, entries[16].id)


class TestSummarizerTopicAwareHandoff:
    def test_folds_at_topic_boundary_when_over_max(self):
        plugin = SummarizerPlugin(max_entries=10, keep_recent=5)
        tape = Tape()
        tape.append(_make_topic_initial("t1", 1))
        for i in range(6):
            tape.append(
                Entry(
                    kind="message", payload={"role": "user", "content": f"t1 msg {i}"}
                )
            )
        tape.append(_make_topic_finalized("t1", files=["src/auth.py"]))
        tape.append(_make_topic_initial("t2", 2))
        for i in range(5):
            tape.append(
                Entry(
                    kind="message", payload={"role": "user", "content": f"t2 msg {i}"}
                )
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert split_point == 8
        assert isinstance(anchor, Anchor)
        assert anchor.is_handoff is True

    def test_no_fold_when_under_max(self):
        plugin = SummarizerPlugin(max_entries=50)
        tape = Tape()
        tape.append(_make_topic_initial("t1"))
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg {i}"})
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is None

    def test_fallback_to_entry_count_when_no_topics(self):
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        for i in range(20):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg {i}"})
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert split_point == 17

    def test_multiple_completed_topics_folds_all(self):
        plugin = SummarizerPlugin(max_entries=10)
        tape = Tape()
        tape.append(_make_topic_initial("t1", 1))
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "t1 work"})
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "t1 done"})
        )
        tape.append(_make_topic_finalized("t1"))
        tape.append(_make_topic_initial("t2", 2))
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "t2 work"})
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "t2 done"})
        )
        tape.append(_make_topic_finalized("t2"))
        tape.append(_make_topic_initial("t3", 3))
        for i in range(4):
            tape.append(
                Entry(
                    kind="message", payload={"role": "user", "content": f"t3 msg {i}"}
                )
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert split_point == 8

    def test_split_never_opens_inside_tool_result_run(self):
        """A window boundary inside a tc/tr group orphans tool results.

        Strict providers (kimi) reject a ``tool`` message whose call was folded
        away; the split must back off to the start of the group.
        """
        plugin = SummarizerPlugin(max_entries=10, keep_recent=4)
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "goal"})
        )
        for i in range(4):
            tape.append(
                Entry(
                    kind="tool_call",
                    payload={"id": f"call_{i}", "name": "bash", "arguments": {}},
                )
            )
        for i in range(4):
            tape.append(
                Entry(
                    kind="tool_result",
                    payload={"tool_call_id": f"call_{i}", "content": "ok"},
                )
            )
        # 9 entries > max 10? pad to exceed the limit.
        for i in range(3):
            tape.append(
                Entry(
                    kind="message",
                    payload={"role": "assistant", "content": f"pad {i}"},
                )
            )
        # len(visible)=12, keep_recent=4 -> raw split at 8, inside tr run.
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, _ = result
        visible = tape.windowed_entries()
        assert visible[split_point].kind == "tool_call"
        assert split_point == 1

    def test_split_inside_tool_call_run_backs_off_to_group_start(self):
        plugin = SummarizerPlugin(max_entries=10, keep_recent=8)
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "goal"})
        )
        for i in range(3):
            tape.append(
                Entry(
                    kind="tool_call",
                    payload={"id": f"call_{i}", "name": "bash", "arguments": {}},
                )
            )
        for i in range(3):
            tape.append(
                Entry(
                    kind="tool_result",
                    payload={"tool_call_id": f"call_{i}", "content": "ok"},
                )
            )
        for i in range(4):
            tape.append(
                Entry(
                    kind="message",
                    payload={"role": "assistant", "content": f"tail {i}"},
                )
            )
        # len=11, keep_recent=8 -> raw split at 3, inside the tc run (tc2).
        # The safe boundary is the group start at index 1.
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, _ = result
        assert split_point == 1
        assert tape.windowed_entries()[split_point].kind == "tool_call"

    def test_visible_slice_has_no_orphan_tool_results(self):
        """Every tool result in the window must have its call visible too."""
        from agentkit.context.builder import ContextBuilder

        plugin = SummarizerPlugin(max_entries=10, keep_recent=4)
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "goal"})
        )
        for i in range(4):
            tape.append(
                Entry(
                    kind="tool_call",
                    payload={"id": f"call_{i}", "name": "bash", "arguments": {}},
                )
            )
            tape.append(
                Entry(
                    kind="tool_result",
                    payload={"tool_call_id": f"call_{i}", "content": "ok"},
                )
            )
        for i in range(3):
            tape.append(
                Entry(
                    kind="message",
                    payload={"role": "assistant", "content": f"pad {i}"},
                )
            )
        # 12 entries; raw split 8 lands inside tr run of call_3's pair.
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, _ = result
        visible = tape.windowed_entries()[split_point:]
        messages = ContextBuilder().build_core_messages(visible)
        call_ids = {
            c["id"]
            for m in messages
            for c in (m.get("tool_calls") or [])
        }
        for m in messages:
            if m.get("role") == "tool":
                assert m["tool_call_id"] in call_ids

    def test_handoff_anchor_contains_topic_summary(self):
        plugin = SummarizerPlugin(max_entries=5)
        tape = Tape()
        tape.append(_make_topic_initial("t1", 1))
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix auth bug"})
        )
        tape.append(_make_topic_finalized("t1", files=["src/auth.py"]))
        tape.append(_make_topic_initial("t2", 2))
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"t2 {i}"})
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        _, anchor = result
        content = anchor.payload.get("content", "").lower()
        assert "topic" in content or "summarized" in content

    def test_find_last_finalized_uses_fold_boundary(self):
        plugin = SummarizerPlugin(max_entries=5)
        entries = [
            Entry(kind="message", payload={"role": "user", "content": "a"}),
            Anchor(
                anchor_type="topic_end",
                payload={"content": "fold"},
            ),
            Entry(kind="message", payload={"role": "user", "content": "b"}),
        ]
        idx = plugin._find_last_finalized(entries)
        assert idx == 1

    def test_find_last_finalized_recognizes_old_meta_anchor_type(self):
        plugin = SummarizerPlugin(max_entries=5)
        entries = [
            Entry(
                kind="anchor",
                payload={"content": "old"},
                meta={"anchor_type": "topic_finalized", "fold_boundary": True},
            ),
            Entry(kind="message", payload={"role": "user", "content": "msg"}),
        ]
        idx = plugin._find_last_finalized(entries)
        assert idx == 0
