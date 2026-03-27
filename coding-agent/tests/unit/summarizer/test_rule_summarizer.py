"""Tests for rule-based summarizer."""

import pytest

from coding_agent.summarizer.rule_summarizer import RuleSummarizer


class TestRuleSummarizer:
    """Tests for rule-based summarizer."""
    
    @pytest.mark.asyncio
    async def test_summarize_basic(self):
        """Test rule-based summarization."""
        summarizer = RuleSummarizer()
        
        messages = [
            {"role": "system", "content": "Task: Implement login"},
            {"role": "user", "content": "How to do it?"},
            {"role": "assistant", "content": "Here is the code", "tool_calls": [{"id": "1"}]},
        ]
        
        summary = await summarizer.summarize(messages)
        
        assert "Task" in summary.content
        assert summary.original_tokens > 0
        assert summary.summary_tokens > 0
        assert len(summary.key_points) > 0
    
    @pytest.mark.asyncio
    async def test_summarize_empty_messages(self):
        """Test summarizing empty list."""
        summarizer = RuleSummarizer()
        
        summary = await summarizer.summarize([])
        
        assert summary.content == "No messages to summarize."
        assert summary.original_tokens == 0
    
    @pytest.mark.asyncio
    async def test_summarize_with_tools(self):
        """Test summarization with tool calls."""
        summarizer = RuleSummarizer()
        
        messages = [
            {"role": "system", "content": "You are a coding assistant"},
            {"role": "user", "content": "Search for files"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "function": {"name": "glob", "arguments": "*.py"}},
                {"id": "2", "function": {"name": "grep", "arguments": "def"}},
            ]},
            {"role": "tool", "content": "result", "tool_call_id": "1"},
        ]
        
        summary = await summarizer.summarize(messages)
        
        assert "Tool Calls" in summary.content
        assert "Tool Results" in summary.content
        assert "Tools Used" in summary.content
        assert "glob" in summary.content
        assert "grep" in summary.content
    
    @pytest.mark.asyncio
    async def test_count_tokens(self):
        """Test token counting."""
        summarizer = RuleSummarizer()
        
        messages = [
            {"role": "user", "content": "x" * 100},  # ~25 tokens
            {"role": "assistant", "content": "y" * 100},  # ~25 tokens
        ]
        
        count = summarizer._count_tokens(messages)
        
        # Should be roughly 50 + overhead (4 per message)
        assert count > 50
        assert count < 70
