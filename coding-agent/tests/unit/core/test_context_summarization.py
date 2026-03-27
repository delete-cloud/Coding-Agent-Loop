"""Tests for context summarization integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from coding_agent.core.context import Context
from coding_agent.core.tape import Entry, Tape
from coding_agent.summarizer.base import Summary
from coding_agent.summarizer.llm_summarizer import LLMSummarizer
from coding_agent.summarizer.rule_summarizer import RuleSummarizer


class TestContextSummarization:
    """Tests for context summarization integration."""
    
    @pytest.mark.asyncio
    async def test_should_summarize_when_over_threshold(self):
        """Test summarization triggers when over threshold."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        # Create a tape with many messages to exceed threshold
        tape = Tape()
        
        # Add enough messages to exceed 80% threshold (800 tokens ~ 3200 chars)
        for i in range(10):
            tape.append(Entry.message("user", "x" * 400))  # ~100 tokens each
            tape.append(Entry.message("assistant", "y" * 400))
        
        # Check token count
        messages = await context.build_working_set(tape)
        total_tokens = context._estimate_tokens(messages)
        
        # Should have triggered summarization (or at least be near threshold)
        # With summarization, the total should be reduced
        assert total_tokens < 2000  # Original would be much higher
    
    @pytest.mark.asyncio
    async def test_should_not_summarize_when_under_threshold(self):
        """Test summarization doesn't trigger when under threshold."""
        context = Context(max_tokens=10000, system_prompt="Test")
        
        tape = Tape()
        tape.append(Entry.message("user", "Hello"))
        tape.append(Entry.message("assistant", "Hi there"))
        
        messages = await context.build_working_set(tape)
        
        # Should not trigger summarization with so few messages
        # Check no summary message was added
        summary_messages = [m for m in messages if "Summary" in m.get("content", "")]
        assert len(summary_messages) == 0
    
    def test_summary_cache_key_stable(self):
        """Test cache key is stable for same messages."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        messages = [
            {"role": "user", "content": "test message"},
            {"role": "assistant", "content": "response"},
        ]
        
        key1 = context._compute_cache_key(messages)
        key2 = context._compute_cache_key(messages)
        
        assert key1 == key2
        assert len(key1) == 16  # MD5 hex truncated to 16 chars
    
    def test_summary_cache_key_different(self):
        """Test cache key is different for different messages."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        messages1 = [{"role": "user", "content": "message A"}]
        messages2 = [{"role": "user", "content": "message B"}]
        
        key1 = context._compute_cache_key(messages1)
        key2 = context._compute_cache_key(messages2)
        
        assert key1 != key2
    
    @pytest.mark.asyncio
    async def test_summarize_messages_with_recent_keeping(self):
        """Test that recent messages are kept intact."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        # Create many messages
        messages = [{"role": "system", "content": "System prompt"}]
        for i in range(20):
            messages.append({"role": "user", "content": f"Message {i}"})
            messages.append({"role": "assistant", "content": f"Response {i}"})
        
        # Mock summarizer
        mock_summary = Summary(
            content="Summary content",
            original_tokens=500,
            summary_tokens=100,
            key_points=["point 1"],
        )
        
        mock_summarizer = MagicMock()
        mock_summarizer.summarize = AsyncMock(return_value=mock_summary)
        context._summarizer = mock_summarizer
        
        # Summarize
        result = await context._summarize_messages(messages, None)
        
        # Should have system + summary + recent messages
        assert len(result) > 2
        
        # Check summary message exists
        summary_msgs = [m for m in result if "Summary" in m.get("content", "")]
        assert len(summary_msgs) == 1
        
        # Recent messages should be preserved (at least KEEP_RECENT messages)
        non_system = [m for m in result if m.get("role") != "system"]
        assert len(non_system) >= context.KEEP_RECENT
    
    @pytest.mark.asyncio
    async def test_cache_used_for_same_messages(self):
        """Test that cache is used when summarizing same messages."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        old_messages = [{"role": "user", "content": "Old message"}]
        
        mock_summary = Summary(
            content="Cached summary",
            original_tokens=100,
            summary_tokens=50,
            key_points=["point"],
        )
        
        mock_summarizer = MagicMock()
        mock_summarizer.summarize = AsyncMock(return_value=mock_summary)
        context._summarizer = mock_summarizer
        
        # Build full message list
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "resp3"},
            {"role": "user", "content": "msg4"},
            {"role": "assistant", "content": "resp4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "resp5"},
            {"role": "user", "content": "msg6"},
            {"role": "assistant", "content": "resp6"},
        ]
        
        # First call - should call summarizer
        await context._summarize_messages(messages, None)
        assert mock_summarizer.summarize.call_count == 1
        
        # Second call with same messages - should use cache
        await context._summarize_messages(messages, None)
        # Summarizer should not be called again
        assert mock_summarizer.summarize.call_count == 1
    
    @pytest.mark.asyncio
    async def test_fallback_to_rule_summarizer_on_error(self):
        """Test fallback to rule summarizer when LLM fails with retryable exception."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        # Create failing summarizer with a retryable exception (RuntimeError)
        failing_summarizer = MagicMock()
        failing_summarizer.summarize = AsyncMock(side_effect=RuntimeError("LLM failed"))
        context._summarizer = failing_summarizer
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "resp3"},
            {"role": "user", "content": "msg4"},
            {"role": "assistant", "content": "resp4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "resp5"},
            {"role": "user", "content": "msg6"},
            {"role": "assistant", "content": "resp6"},
        ]
        
        # Should not raise, should use fallback
        result = await context._summarize_messages(messages, None)
        
        # Should have summary from rule summarizer
        summary_msgs = [m for m in result if "Summary" in m.get("content", "")]
        assert len(summary_msgs) == 1
        assert "Conversation Summary" in summary_msgs[0]["content"]
    
    @pytest.mark.asyncio
    async def test_no_summarization_when_too_few_messages(self):
        """Test that summarization is skipped with few messages."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
        ]
        
        result = await context._summarize_messages(messages, None)
        
        # Should return original messages unchanged
        assert result == messages
    
    def test_estimate_tokens_with_string(self):
        """Test token estimation with string input."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        tokens = context._estimate_tokens("x" * 100)
        
        assert tokens == 25  # 100 / 4
    
    def test_estimate_tokens_with_messages(self):
        """Test token estimation with message list input."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        messages = [
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
        ]
        
        tokens = context._estimate_tokens(messages)
        
        # ~50 tokens for content + 8 for overhead
        assert tokens > 50
    
    @pytest.mark.asyncio
    async def test_lazy_summarizer_initialization(self):
        """Test that summarizer is lazily initialized from provider."""
        mock_provider = MagicMock()
        context = Context(max_tokens=1000, system_prompt="Test")
        
        # Create messages that would trigger summarization
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "x" * 400},  # ~100 tokens
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
        ]
        
        # Verify messages exceed threshold
        total_tokens = context._estimate_tokens(messages)
        threshold = int(1000 * 0.8)
        assert total_tokens > threshold, f"Expected {total_tokens} > {threshold}"
        
        # Mock LLMSummarizer creation
        with patch("coding_agent.core.context.LLMSummarizer") as mock_summarizer_class:
            mock_summarizer = MagicMock()
            mock_summarizer.summarize = AsyncMock(return_value=Summary(
                content="Test summary",
                original_tokens=500,
                summary_tokens=50,
                key_points=["point"],
            ))
            mock_summarizer_class.return_value = mock_summarizer
            
            # Call _summarize_messages directly with provider
            await context._summarize_messages(messages, provider=mock_provider)
            
            # Should have created LLMSummarizer with provider
            mock_summarizer_class.assert_called_once_with(mock_provider)
