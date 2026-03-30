import pytest
from coding_agent.plugins.summarizer import SummarizerPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


def _make_topic_initial(topic_id: str, topic_number: int = 1) -> Entry:
    return Entry(
        kind="anchor",
        payload={"content": f"Topic #{topic_number}"},
        meta={
            "anchor_type": "topic_initial",
            "topic_id": topic_id,
            "topic_number": topic_number,
        },
    )


def _make_topic_finalized(topic_id: str, files: list[str] | None = None) -> Entry:
    return Entry(
        kind="anchor",
        payload={"content": f"Topic involved files: {', '.join(files or [])}"},
        meta={
            "anchor_type": "topic_finalized",
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
        assert result is None  # No windowing needed

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
        assert anchor.kind == "anchor"
        assert anchor.meta.get("anchor_type") == "handoff"
        assert "source_entry_count" in anchor.meta

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
        assert split_point == len(list(tape)) - 3  # 20 - 3 = 17

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


class TestSummarizerTopicAwareHandoff:
    """P2: topic-boundary windowing instead of entry-count truncation."""

    def test_folds_at_topic_boundary_when_over_max(self):
        plugin = SummarizerPlugin(max_entries=10, keep_recent=5)
        tape = Tape()
        # Completed topic 1: 8 entries
        tape.append(_make_topic_initial("t1", 1))
        for i in range(6):
            tape.append(
                Entry(
                    kind="message", payload={"role": "user", "content": f"t1 msg {i}"}
                )
            )
        tape.append(_make_topic_finalized("t1", files=["src/auth.py"]))
        # Active topic 2: 6 entries
        tape.append(_make_topic_initial("t2", 2))
        for i in range(5):
            tape.append(
                Entry(
                    kind="message", payload={"role": "user", "content": f"t2 msg {i}"}
                )
            )
        # Total: 14 entries > max_entries=10
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert split_point == 8  # after topic_finalized (index 7 → split_point = 8)
        assert anchor.kind == "anchor"
        assert anchor.meta.get("anchor_type") == "handoff"

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
        assert split_point == 17  # 20 - 3

    def test_multiple_completed_topics_folds_all(self):
        plugin = SummarizerPlugin(max_entries=10)
        tape = Tape()
        # Topic 1: 4 entries
        tape.append(_make_topic_initial("t1", 1))
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "t1 work"})
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "t1 done"})
        )
        tape.append(_make_topic_finalized("t1"))
        # Topic 2: 4 entries
        tape.append(_make_topic_initial("t2", 2))
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "t2 work"})
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "t2 done"})
        )
        tape.append(_make_topic_finalized("t2"))
        # Topic 3 (active): 5 entries
        tape.append(_make_topic_initial("t3", 3))
        for i in range(4):
            tape.append(
                Entry(
                    kind="message", payload={"role": "user", "content": f"t3 msg {i}"}
                )
            )
        # Total: 13 > max=10. Last finalized is t2 at index 7 → split_point=8
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert split_point == 8  # after t2's topic_finalized

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
