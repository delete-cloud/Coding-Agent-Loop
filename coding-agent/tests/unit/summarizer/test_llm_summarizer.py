"""Tests for LLM summarizer."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from coding_agent.summarizer.llm_summarizer import LLMSummarizer
from coding_agent.providers.base import StreamEvent


class TestLLMSummarizer:
    """Tests for LLM summarizer."""
    
    @pytest.mark.asyncio
    async def test_summarize_conversation(self):
        """Test basic summarization."""
        # Mock provider
        mock_provider = MagicMock()
        
        async def mock_stream(messages, tools=None, **kwargs):
            # Simulate streaming response
            yield StreamEvent(type="delta", text="**Task**: Implement feature")
            yield StreamEvent(type="delta", text="\n**Decisions**: Use Python")
            yield StreamEvent(type="done")
        
        mock_provider.stream = mock_stream
        
        summarizer = LLMSummarizer(mock_provider)
        
        messages = [
            {"role": "system", "content": "You are a coding assistant"},
            {"role": "user", "content": "Help me implement a feature"},
            {"role": "assistant", "content": "I'll help you implement it"},
        ]
        
        summary = await summarizer.summarize(messages)
        
        assert summary.content
        assert summary.original_tokens > 0
        assert summary.summary_tokens > 0
        assert len(summary.key_points) > 0
    
    @pytest.mark.asyncio
    async def test_summarize_empty_messages(self):
        """Test summarizing empty list."""
        mock_provider = MagicMock()
        summarizer = LLMSummarizer(mock_provider)
        
        summary = await summarizer.summarize([])
        
        assert summary.content == "No messages to summarize."
        assert summary.original_tokens == 0
        assert summary.summary_tokens == 0
        assert summary.key_points == []
    
    @pytest.mark.asyncio
    async def test_summarize_with_tool_calls(self):
        """Test summarizing messages with tool calls."""
        mock_provider = MagicMock()
        
        async def mock_stream(messages, tools=None, **kwargs):
            yield StreamEvent(type="delta", text="**Task**: Read files\n**Decisions**: Use grep")
            yield StreamEvent(type="done")
        
        mock_provider.stream = mock_stream
        
        summarizer = LLMSummarizer(mock_provider)
        
        messages = [
            {"role": "user", "content": "Find all Python files"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "1", "function": {"name": "glob", "arguments": "*.py"}}
            ]},
            {"role": "tool", "content": "file1.py, file2.py", "tool_call_id": "1"},
        ]
        
        summary = await summarizer.summarize(messages)
        
        assert summary.content
        assert "Task" in summary.content
    
    @pytest.mark.asyncio
    async def test_extract_key_points(self):
        """Test key point extraction."""
        summarizer = LLMSummarizer(MagicMock())
        
        text = """**Task**: Implement login
**Decisions**: Use OAuth2
- Use JWT tokens
• Secure cookies"""
        
        points = summarizer._extract_key_points(text)
        
        assert len(points) == 4
        assert "Task**: Implement login" in points or "Task: Implement login" in points
        assert "Decisions**: Use OAuth2" in points or "Decisions: Use OAuth2" in points
    
    @pytest.mark.asyncio
    async def test_format_conversation_with_long_content(self):
        """Test conversation formatting truncates long content."""
        summarizer = LLMSummarizer(MagicMock())
        
        messages = [
            {"role": "user", "content": "x" * 500},  # Very long content
        ]
        
        formatted = summarizer._format_conversation(messages)
        
        assert "..." in formatted  # Should be truncated
        assert len(formatted) < 500
    
    @pytest.mark.asyncio
    async def test_count_tokens(self):
        """Test token counting."""
        summarizer = LLMSummarizer(MagicMock())
        
        messages = [
            {"role": "user", "content": "x" * 100},  # ~25 tokens
            {"role": "assistant", "content": "y" * 100},  # ~25 tokens
        ]
        
        count = summarizer._count_tokens(messages)
        
        # Should be roughly 50 + overhead (4 per message)
        assert count > 50
        assert count < 70
