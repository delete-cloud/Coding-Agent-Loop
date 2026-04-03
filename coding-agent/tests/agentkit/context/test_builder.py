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

    def test_anchor_with_prefix_rendered(self):
        """Anchors with meta.prefix get [Prefix] prepended."""
        tape = Tape()
        tape.append(
            Entry(
                kind="anchor",
                payload={"content": "Earlier conversation about auth module"},
                meta={"prefix": "Context Summary"},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "continue"})
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3
        assert messages[1]["role"] == "system"
        assert messages[1]["content"].startswith("[Context Summary]")
        assert "auth module" in messages[1]["content"]

    def test_anchor_with_skip_omitted(self):
        """Anchors with meta.skip=True are not rendered."""
        tape = Tape()
        tape.append(
            Entry(
                kind="anchor",
                payload={"content": "Auth bug fixed successfully"},
                meta={"skip": True},
            )
        )
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "next task"})
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2  # system + user only

    def test_plain_anchor_unchanged(self):
        """Anchors without meta.prefix or meta.skip behave as before."""
        tape = Tape()
        tape.append(Entry(kind="anchor", payload={"content": "Important context"}))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3
        assert messages[1]["content"] == "Important context"

    def test_anchor_fold_boundary_skipped(self):
        from agentkit.tape.anchor import Anchor

        tape = Tape()
        tape.append(
            Anchor(
                anchor_type="topic_end",
                payload={"content": "topic done"},
                meta={"topic_id": "t1"},
            )
        )
        tape.append(Entry(kind="message", payload={"role": "user", "content": "next"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2

    def test_anchor_handoff_rendered_as_system(self):
        from agentkit.tape.anchor import Anchor

        tape = Tape()
        tape.append(
            Anchor(
                anchor_type="handoff",
                payload={"content": "Earlier context summary"},
                meta={"prefix": "Context Summary"},
            )
        )
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3
        assert messages[1]["role"] == "system"
        assert messages[1]["content"].startswith("[Context Summary]")

    def test_anchor_topic_start_rendered(self):
        from agentkit.tape.anchor import Anchor

        tape = Tape()
        tape.append(
            Anchor(
                anchor_type="topic_start",
                payload={"content": "New topic about auth"},
                meta={"prefix": "Topic Start"},
            )
        )
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3
        assert "[Topic Start]" in messages[1]["content"]

    def test_reasoning_content_on_message_entry(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={
                    "role": "assistant",
                    "content": "The answer is 42.",
                    "reasoning_content": "Let me think step by step...",
                },
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert messages[1]["reasoning_content"] == "Let me think step by step..."
        assert messages[1]["content"] == "The answer is 42."

    def test_reasoning_content_absent_when_not_in_payload(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "Hello"},
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert "reasoning_content" not in messages[1]

    def test_reasoning_content_on_tool_call_entry(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "id": "tc_1",
                    "name": "bash",
                    "arguments": {"cmd": "ls"},
                    "role": "assistant",
                    "reasoning_content": "I should list files first.",
                },
            )
        )
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert messages[1]["reasoning_content"] == "I should list files first."
        assert len(messages[1]["tool_calls"]) == 1

    def test_reasoning_content_merged_from_preceding_text(self):
        tape = Tape()
        tape.append(
            Entry(
                kind="message",
                payload={
                    "role": "assistant",
                    "content": "Let me check.",
                    "reasoning_content": "Thinking about approach...",
                },
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
        merged = messages[1]
        assert merged["content"] == "Let me check."
        assert merged["reasoning_content"] == "Thinking about approach..."
        assert len(merged["tool_calls"]) == 1


from agentkit.tape.view import TapeView


class TestContextBuilderWithView:
    def test_build_from_view(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "hi"})
        )
        view = TapeView.from_tape(tape)
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(view)
        assert len(messages) == 3
        assert messages[1]["content"] == "hello"
        assert messages[2]["content"] == "hi"

    def test_build_from_view_with_grounding(self):
        tape = Tape()
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix the bug"})
        )
        view = TapeView.from_tape(tape)
        builder = ContextBuilder(system_prompt="system")
        grounding = [{"role": "system", "content": "[Memory] Use pytest"}]
        messages = builder.build(view, grounding=grounding)
        assert len(messages) == 3
        assert messages[1]["content"] == "[Memory] Use pytest"

    def test_build_from_windowed_view(self):
        tape = Tape()
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"old-{i}"})
            )
        anchor = Entry(
            kind="anchor",
            payload={"content": "summary of old messages"},
            meta={"is_handoff": True, "prefix": "Context Summary"},
        )
        tape.handoff(anchor)
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "new message"})
        )
        view = TapeView.from_tape(tape)
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(view)
        assert len(messages) == 3
        assert "[Context Summary]" in messages[1]["content"]
        assert messages[2]["content"] == "new message"

    def test_build_from_tape_still_works(self):
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2

    def test_build_with_explicit_entries_uses_provided_list(self):
        tape = Tape()
        for i in range(5):
            tape.append(
                Entry(kind="message", payload={"role": "user", "content": f"tape-{i}"})
            )
        explicit = [
            Entry(kind="message", payload={"role": "user", "content": "explicit-0"}),
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "explicit-1"},
            ),
        ]
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape, entries=explicit)
        assert len(messages) == 3  # system + 2 explicit
        assert messages[1]["content"] == "explicit-0"
        assert messages[2]["content"] == "explicit-1"
