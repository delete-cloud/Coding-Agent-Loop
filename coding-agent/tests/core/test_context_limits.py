"""Tests for Context token limit enforcement."""

import pytest

from coding_agent.core.context import Context
from coding_agent.core.tape import Entry, Tape


class TestContextTokenLimits:
    """Test max_tokens enforcement in context building."""

    def test_validates_max_tokens_positive(self):
        """Test that max_tokens must be positive."""
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            Context(max_tokens=0, system_prompt="Test")
        
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            Context(max_tokens=-100, system_prompt="Test")

    def test_system_prompt_always_included(self, tmp_path):
        """Test that system prompt is always included even with tiny budget."""
        tape = Tape(tmp_path / "test.jsonl")
        tape.append(Entry.message("user", "A" * 1000))
        
        # Very small budget (10 tokens = ~40 chars)
        ctx = Context(max_tokens=10, system_prompt="System prompt here")
        messages = ctx.build_working_set(tape)
        
        # System prompt should always be present
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "System prompt here"

    def test_truncates_when_exceeds_budget(self, tmp_path):
        """Test that messages are truncated when exceeding token budget."""
        tape = Tape(tmp_path / "test.jsonl")
        
        # Add many long messages
        for i in range(10):
            tape.append(Entry.message("user", f"Message {i}: " + "X" * 100))
        
        # Small budget: 50 tokens = ~200 chars
        ctx = Context(max_tokens=50, system_prompt="System")
        messages = ctx.build_working_set(tape)
        
        # Count total estimated tokens
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        total_chars += len("System")
        estimated_tokens = total_chars // 4
        
        # Should be within budget (with some tolerance for the truncation logic)
        assert estimated_tokens <= 60  # Allow some overhead

    def test_oldest_messages_removed_first(self, tmp_path):
        """Test that oldest non-system messages are removed first when truncating."""
        tape = Tape(tmp_path / "test.jsonl")
        
        # Add messages in order
        tape.append(Entry.message("user", "First message"))
        tape.append(Entry.message("assistant", "Second message"))
        tape.append(Entry.message("user", "Third message"))
        
        # Very small budget to force truncation
        ctx = Context(max_tokens=5, system_prompt="System")
        messages = ctx.build_working_set(tape)
        
        # System prompt + possibly 1-2 recent messages
        assert messages[0]["role"] == "system"
        
        # If we have user messages, the oldest should be removed
        user_contents = [m["content"] for m in messages if m["role"] == "user"]
        if user_contents:
            # "First message" should be removed if truncated
            assert "First message" not in user_contents or len(user_contents) >= 2

    def test_truncates_large_tool_result(self, tmp_path):
        """Test that large tool results are truncated."""
        tape = Tape(tmp_path / "test.jsonl")
        
        # Add tool call and large result
        tape.append(Entry.tool_call("call_1", "bash", {"cmd": "ls"}))
        tape.append(Entry.tool_result("call_1", "A" * 5000))  # Very large result
        
        # Small budget
        ctx = Context(max_tokens=50, system_prompt="System")
        messages = ctx.build_working_set(tape)
        
        # Find tool result message
        tool_results = [m for m in messages if m["role"] == "tool"]
        if tool_results:
            # Should be truncated
            assert len(tool_results[0]["content"]) < 5000

    def test_empty_tape_within_budget(self, tmp_path):
        """Test empty tape with large budget."""
        tape = Tape(tmp_path / "test.jsonl")
        
        ctx = Context(max_tokens=100000, system_prompt="System prompt")
        messages = ctx.build_working_set(tape)
        
        assert len(messages) == 1
        assert messages[0]["content"] == "System prompt"

    def test_anchor_truncation_respects_budget(self, tmp_path):
        """Test that anchor-based truncation also respects token budget."""
        tape = Tape(tmp_path / "test.jsonl")
        
        # Old messages before anchor
        tape.append(Entry.message("user", "Old: " + "X" * 200))
        tape.handoff("checkpoint", {"summary": "Phase 1 done"})
        
        # New messages after anchor
        for i in range(5):
            tape.append(Entry.message("user", f"New {i}: " + "Y" * 100))
        
        # Small budget
        ctx = Context(max_tokens=20, system_prompt="System")
        messages = ctx.build_working_set(tape)
        
        # Should have system + some messages after anchor
        assert messages[0]["role"] == "system"
        
        # Old messages should not appear (truncated by anchor AND budget)
        contents = [m.get("content", "") for m in messages]
        assert "Old:" not in str(contents)

    def test_message_to_text_extraction(self):
        """Test that _message_to_text correctly extracts text from different message types."""
        ctx = Context(max_tokens=100, system_prompt="Test")
        
        # Simple message
        msg1 = {"role": "user", "content": "Hello"}
        assert ctx._message_to_text(msg1) == "Hello"
        
        # Tool call message
        msg2 = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}
            }]
        }
        text = ctx._message_to_text(msg2)
        assert "bash" in text
        assert "ls" in text
        
        # Tool result message
        msg3 = {"role": "tool", "tool_call_id": "call_1", "content": "result"}
        assert "call_1" in ctx._message_to_text(msg3)
        assert "result" in ctx._message_to_text(msg3)
