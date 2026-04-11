import pytest
from coding_agent.plugins.summarizer import SummarizerPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestSummarizerPlugin:
    def test_state_key(self):
        plugin = SummarizerPlugin(max_entries=10)
        assert plugin.state_key == "summarizer"

    def test_hooks_include_summarize_context(self):
        plugin = SummarizerPlugin(max_entries=10)
        hooks = plugin.hooks()
        assert "summarize_context" in hooks

    def test_short_tape_unchanged(self):
        plugin = SummarizerPlugin(max_entries=100)
        tape = Tape()
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg {i}"})
            )
        result = plugin.summarize_context(tape=tape)
        assert result is None  # No summarization needed

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
        result = plugin.summarize_context(tape=tape)
        assert result is not None
        assert len(result) < 20  # Summarized tape has fewer entries

    def test_preserves_recent_entries(self):
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        for i in range(20):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"msg-{i}"})
            )
        result = plugin.summarize_context(tape=tape)
        if result is not None:
            # Last entries should be the most recent ones
            last_contents = [e.payload["content"] for e in result[-3:]]
            assert "msg-19" in last_contents
