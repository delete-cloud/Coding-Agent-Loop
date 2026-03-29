import pytest
from agentkit.context.builder import ContextBuilder
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class TestContextBuilder:
    def test_empty_tape_returns_system_only(self):
        tape = Tape()
        builder = ContextBuilder(system_prompt="You are a helpful agent.")
        messages = builder.build(tape)
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful agent."

    def test_message_entries_become_messages(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "hi"})
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3  # system + user + assistant
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    def test_tool_call_entries_become_assistant_tool_use(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="tool_call",
                payload={"id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}},
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2  # system + tool_call
        assert messages[1]["role"] == "assistant"

    def test_tool_result_entries_become_tool_messages(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="tool_result",
                payload={"tool_call_id": "tc_1", "content": "file1.py\nfile2.py"},
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert messages[1]["role"] == "tool"

    def test_grounding_injected_before_last_user_message(self):
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix the bug"})
        )
        builder = ContextBuilder(system_prompt="system")
        grounding = [{"role": "system", "content": "[Memory] User prefers Python."}]
        messages = builder.build(tape, grounding=grounding)
        # system + grounding + user
        assert len(messages) == 3
        assert messages[1]["content"] == "[Memory] User prefers Python."
        assert messages[2]["content"] == "fix the bug"

    def test_anchor_entries_are_preserved(self):
        tape = Tape()
        tape.append(Entry(kind="anchor", payload={"content": "Important context"}))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        # system + anchor-as-system + user
        assert len(messages) == 3

    def test_event_entries_are_skipped(self):
        tape = Tape()
        tape.append(Entry(kind="event", payload={"type": "metrics", "data": {}}))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2  # system + user (event skipped)
