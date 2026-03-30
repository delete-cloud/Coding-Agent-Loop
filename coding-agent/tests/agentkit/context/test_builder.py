import pytest
from agentkit.context.builder import ContextBuilder
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
import json


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
        tool_call = messages[1]["tool_calls"][0]
        assert tool_call["id"] == "tc_1"
        assert tool_call["function"]["name"] == "bash"
        assert tool_call["function"]["arguments"] == json.dumps({"cmd": "ls"})

    def test_tool_call_list_entries_become_assistant_tool_use(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}},
                        {
                            "id": "tc_2",
                            "name": "grep",
                            "arguments": {"pattern": "TODO"},
                        },
                    ],
                },
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)

        assert len(messages) == 2
        assert len(messages[1]["tool_calls"]) == 2
        assert messages[1]["tool_calls"][0]["function"]["arguments"] == json.dumps(
            {"cmd": "ls"}
        )
        assert messages[1]["tool_calls"][1]["function"]["arguments"] == json.dumps(
            {"pattern": "TODO"}
        )

    def test_consecutive_tool_call_entries_merge_into_one_assistant_message(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="tool_call",
                payload={"id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}},
            )
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "id": "tc_2",
                    "name": "grep",
                    "arguments": {"pattern": "TODO"},
                },
            )
        )

        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)

        assert len(messages) == 2
        assert messages[1]["role"] == "assistant"
        assert len(messages[1]["tool_calls"]) == 2
        assert messages[1]["tool_calls"][0]["function"]["name"] == "bash"
        assert messages[1]["tool_calls"][1]["function"]["name"] == "grep"

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

    def test_assistant_text_then_tool_calls_merged_into_single_message(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "Let me check."},
            )
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={"id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}},
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3  # system + user + merged-assistant
        merged = messages[2]
        assert merged["role"] == "assistant"
        assert merged["content"] == "Let me check."
        assert len(merged["tool_calls"]) == 1
        assert merged["tool_calls"][0]["function"]["name"] == "bash"

    def test_assistant_text_without_tool_calls_stays_separate(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "thinking..."},
            )
        )
        tape.append(Entry(kind="message", payload={"role": "user", "content": "ok"}))
        tape.append(
            Entry(
                kind="tool_call",
                payload={"id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}},
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        # system + assistant-text + user + assistant-tool_call (no merge: user in between)
        assert len(messages) == 4
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "thinking..."
        assert "tool_calls" not in messages[1]
        assert messages[3]["role"] == "assistant"
        assert messages[3]["content"] is None

    def test_event_entries_are_skipped(self):
        tape = Tape()
        tape.append(Entry(kind="event", payload={"type": "metrics", "data": {}}))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2  # system + user (event skipped)

    def test_handoff_anchor_rendered_with_prefix(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="anchor",
                payload={"content": "Earlier conversation about auth module"},
                meta={"anchor_type": "handoff"},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "continue"})
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        # system + anchor + user
        assert len(messages) == 3
        assert messages[1]["role"] == "system"
        assert messages[1]["content"].startswith("[Context Summary]")
        assert "auth module" in messages[1]["content"]

    def test_topic_initial_anchor_rendered_with_prefix(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="anchor",
                payload={"content": "Fix authentication bug"},
                meta={"anchor_type": "topic_initial", "topic_id": "t-001"},
            )
        )
        tape.append(Entry(kind="message", payload={"role": "user", "content": "start"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3
        assert messages[1]["role"] == "system"
        assert messages[1]["content"].startswith("[Topic Start]")

    def test_topic_finalized_anchor_skipped(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="anchor",
                payload={"content": "Auth bug fixed successfully"},
                meta={"anchor_type": "topic_finalized", "topic_id": "t-001"},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "next task"})
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        # system + user only (topic_finalized anchor skipped)
        assert len(messages) == 2

    def test_plain_anchor_unchanged(self):
        """Anchors without meta.anchor_type behave as before."""
        tape = Tape()
        tape.append(Entry(kind="anchor", payload={"content": "Important context"}))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3
        assert messages[1]["content"] == "Important context"
