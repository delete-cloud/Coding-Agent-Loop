import pytest
from coding_agent.plugins.summarizer import SummarizerPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


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
