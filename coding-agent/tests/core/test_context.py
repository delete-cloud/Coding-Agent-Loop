"""Tests for Context working set assembly."""

import pytest

from coding_agent.core.context import Context
from coding_agent.core.tape import Entry, Tape


class TestContextWorkingSet:
    """Tests for Context.build_working_set()."""

    def test_system_prompt_always_first(self):
        """System prompt should always be the first message."""
        context = Context(max_tokens=4000, system_prompt="You are a helpful assistant.")
        tape = Tape(path=None)
        tape.append(Entry.message("user", "Hello"))
        
        messages = context.build_working_set(tape)
        
        assert len(messages) >= 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."

    def test_basic_working_set_with_messages(self):
        """Basic working set with system prompt + tape entries as messages."""
        context = Context(max_tokens=4000, system_prompt="You are a helpful assistant.")
        tape = Tape(path=None)
        tape.append(Entry.message("user", "Hello"))
        tape.append(Entry.message("assistant", "Hi there!"))
        
        messages = context.build_working_set(tape)
        
        assert len(messages) == 3  # system + user + assistant
        assert messages[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert messages[1] == {"role": "user", "content": "Hello"}
        assert messages[2] == {"role": "assistant", "content": "Hi there!"}

    def test_anchor_truncation_excludes_older_entries(self):
        """Entries before the most recent anchor should be excluded."""
        context = Context(max_tokens=4000, system_prompt="System prompt")
        tape = Tape(path=None)
        tape.append(Entry.message("user", "Old message"))
        tape.handoff(name="checkpoint1", state={"summary": "Checkpoint reached"})
        tape.append(Entry.message("user", "New message"))
        
        messages = context.build_working_set(tape)
        
        # Should have system + anchor + new message
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert "Old message" not in str(messages)
        assert "New message" in str(messages)

    def test_anchor_is_converted_to_system_message(self):
        """Anchor entry should be converted to a system checkpoint message."""
        context = Context(max_tokens=4000, system_prompt="System")
        tape = Tape(path=None)
        tape.handoff(name="phase1", state={"summary": "Phase 1 complete"})
        
        messages = context.build_working_set(tape)
        
        assert len(messages) == 2  # system + anchor checkpoint
        assert messages[1]["role"] == "system"
        assert "Checkpoint" in messages[1]["content"]
        assert "phase1" in messages[1]["content"]
        assert "Phase 1 complete" in messages[1]["content"]

    def test_multiple_anchors_uses_most_recent(self):
        """When multiple anchors exist, start from the most recent one."""
        context = Context(max_tokens=4000, system_prompt="System")
        tape = Tape(path=None)
        tape.append(Entry.message("user", "Before first anchor"))
        tape.handoff(name="anchor1", state={})
        tape.append(Entry.message("user", "Between anchors"))
        tape.handoff(name="anchor2", state={})
        tape.append(Entry.message("user", "After second anchor"))
        
        messages = context.build_working_set(tape)
        
        # Should only include from anchor2 onwards
        assert "Before first anchor" not in str(messages)
        assert "Between anchors" not in str(messages)
        assert "After second anchor" in str(messages)
        assert "anchor2" in str(messages)

    def test_event_entries_excluded(self):
        """Event entries should be excluded from the working set."""
        context = Context(max_tokens=4000, system_prompt="System")
        tape = Tape(path=None)
        tape.append(Entry.message("user", "Hello"))
        tape.append(Entry.event("internal", {"key": "value"}))
        tape.append(Entry.message("assistant", "Hi"))
        
        messages = context.build_working_set(tape)
        
        # Should have system + user + assistant (no event)
        assert len(messages) == 3
        assert "internal" not in str(messages)
        assert "event" not in str([m["role"] for m in messages])

    def test_tool_call_entry_conversion(self):
        """Tool call entries should be converted to OpenAI tool_calls format."""
        context = Context(max_tokens=4000, system_prompt="System")
        tape = Tape(path=None)
        tape.append(Entry.tool_call("call_123", "read_file", {"path": "/tmp/test.txt"}))
        
        messages = context.build_working_set(tape)
        
        assert len(messages) == 2  # system + tool_call
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == ""
        assert "tool_calls" in messages[1]
        assert len(messages[1]["tool_calls"]) == 1
        assert messages[1]["tool_calls"][0]["id"] == "call_123"
        assert messages[1]["tool_calls"][0]["type"] == "function"
        assert messages[1]["tool_calls"][0]["function"]["name"] == "read_file"

    def test_tool_result_entry_conversion(self):
        """Tool result entries should be converted to OpenAI tool format."""
        context = Context(max_tokens=4000, system_prompt="System")
        tape = Tape(path=None)
        tape.append(Entry.tool_result("call_123", "File contents here"))
        
        messages = context.build_working_set(tape)
        
        assert len(messages) == 2  # system + tool_result
        assert messages[1]["role"] == "tool"
        assert messages[1]["tool_call_id"] == "call_123"
        assert messages[1]["content"] == "File contents here"

    def test_full_tool_call_workflow(self):
        """Complete workflow: assistant tool_calls followed by tool result."""
        context = Context(max_tokens=4000, system_prompt="System")
        tape = Tape(path=None)
        tape.append(Entry.message("user", "Read a file"))
        tape.append(Entry.tool_call("call_abc", "read_file", {"path": "/tmp/test.txt"}))
        tape.append(Entry.tool_result("call_abc", "Hello World"))
        
        messages = context.build_working_set(tape)
        
        assert len(messages) == 4  # system + user + assistant + tool
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["tool_calls"][0]["id"] == "call_abc"
        assert messages[3]["role"] == "tool"
        assert messages[3]["tool_call_id"] == "call_abc"

    def test_empty_tape_returns_only_system(self):
        """Empty tape should return only the system prompt."""
        context = Context(max_tokens=4000, system_prompt="You are helpful.")
        tape = Tape(path=None)
        
        messages = context.build_working_set(tape)
        
        assert len(messages) == 1
        assert messages[0] == {"role": "system", "content": "You are helpful."}

    def test_anchor_with_default_summary(self):
        """Anchor without summary should use default message."""
        context = Context(max_tokens=4000, system_prompt="System")
        tape = Tape(path=None)
        tape.handoff(name="phase1", state={})  # No summary in state
        
        messages = context.build_working_set(tape)
        
        assert "Phase: phase1" in messages[1]["content"]

    def test_unknown_entry_kind_is_excluded(self):
        """Unknown entry kinds should be silently excluded."""
        context = Context(max_tokens=4000, system_prompt="System")
        
        # Test _entry_to_message directly with unknown kind
        from coding_agent.core.tape import Entry
        unknown_entry = Entry(id=1, kind="unknown_kind", payload={"data": "test"})
        result = context._entry_to_message(unknown_entry)
        
        # Should return None for unknown kinds
        assert result is None


class TestContextConfiguration:
    """Tests for Context configuration."""

    def test_context_stores_max_tokens(self):
        """Context should store max_tokens parameter."""
        context = Context(max_tokens=8000, system_prompt="System")
        assert context.max_tokens == 8000

    def test_context_stores_system_prompt(self):
        """Context should store system_prompt parameter."""
        context = Context(max_tokens=4000, system_prompt="Custom prompt")
        assert context.system_prompt == "Custom prompt"


# --- Plan Injection Tests (P1) ---

from coding_agent.core.planner import PlanManager


class TestPlanInjection:
    def test_no_plan_injected_when_none(self):
        ctx = Context(max_tokens=100000, system_prompt="You are an agent.")
        tape = Tape(path=None)
        tape.append(Entry.message("user", "hello"))
        msgs = ctx.build_working_set(tape)
        # Only system + user, no plan message
        assert len(msgs) == 2

    def test_plan_injected_after_system(self):
        planner = PlanManager()
        planner.set_tasks([
            {"title": "Read code", "status": "todo"},
            {"title": "Write tests", "status": "todo"},
        ])
        ctx = Context(max_tokens=100000, system_prompt="You are an agent.", planner=planner)
        tape = Tape(path=None)
        tape.append(Entry.message("user", "hello"))
        msgs = ctx.build_working_set(tape)
        # system + plan + user
        assert len(msgs) == 3
        assert msgs[1]["role"] == "system"
        assert "Current Plan" in msgs[1]["content"]
        assert "[ ] 1. Read code" in msgs[1]["content"]

    def test_empty_plan_not_injected(self):
        planner = PlanManager()
        ctx = Context(max_tokens=100000, system_prompt="You are an agent.", planner=planner)
        tape = Tape(path=None)
        tape.append(Entry.message("user", "hello"))
        msgs = ctx.build_working_set(tape)
        # Empty plan should not inject a message
        assert len(msgs) == 2
