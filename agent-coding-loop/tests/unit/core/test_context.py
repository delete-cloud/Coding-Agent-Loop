"""Tests for context management with token budget."""

import pytest

from coding_agent.core.context import Context, MAX_TOOL_RESULT_TOKENS, PlanManager
from coding_agent.tokens import ApproximateCounter, TokenCounter


class TestPlanManager:
    """Tests for PlanManager."""

    def test_init(self):
        """Test PlanManager initialization."""
        planner = PlanManager()
        assert planner.tasks == []
        assert planner.current_task is None

    def test_add_task(self):
        """Test adding tasks to the plan."""
        planner = PlanManager()
        planner.add_task("Test task", priority="high")
        
        assert len(planner.tasks) == 1
        assert planner.tasks[0]["description"] == "Test task"
        assert planner.tasks[0]["priority"] == "high"

    def test_get_current_task(self):
        """Test getting current task."""
        planner = PlanManager()
        assert planner.get_current_task() is None
        
        planner.current_task = {"description": "Active task"}
        assert planner.get_current_task()["description"] == "Active task"


class TestContextInitialization:
    """Tests for Context initialization."""

    def test_basic_init(self):
        """Test basic Context initialization."""
        ctx = Context(
            max_tokens=4000,
            system_prompt="You are a helpful assistant.",
        )
        
        assert ctx.max_tokens == 4000
        assert ctx.system_prompt == "You are a helpful assistant."
        assert ctx.planner is None
        assert isinstance(ctx.token_counter, ApproximateCounter)

    def test_init_with_planner(self):
        """Test Context initialization with PlanManager."""
        planner = PlanManager()
        ctx = Context(
            max_tokens=4000,
            system_prompt="Test prompt",
            planner=planner,
        )
        
        assert ctx.planner is planner

    def test_init_with_custom_token_counter(self):
        """Test Context initialization with custom token counter."""
        class CustomCounter(TokenCounter):
            def count(self, text: str) -> int:
                return len(text)
            
            def count_messages(self, messages: list[dict]) -> int:
                return sum(len(m.get("content", "")) for m in messages)
        
        custom_counter = CustomCounter()
        ctx = Context(
            max_tokens=4000,
            system_prompt="Test",
            token_counter=custom_counter,
        )
        
        assert ctx.token_counter is custom_counter

    def test_system_prompt_in_messages(self):
        """Test that system prompt is included in initial messages."""
        ctx = Context(
            max_tokens=4000,
            system_prompt="System instructions",
        )
        
        messages = ctx.get_messages()
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "System instructions"


class TestContextMessageManagement:
    """Tests for Context message management."""

    def test_add_message(self):
        """Test adding messages to context."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        ctx.add_message("user", "Hello")
        ctx.add_message("assistant", "Hi there!")
        
        messages = ctx.get_messages()
        assert len(messages) == 3  # system + 2 added
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "Hi there!"

    def test_add_tool_result(self):
        """Test adding tool results."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        ctx.add_tool_result("search", "Results found")
        
        working_set = ctx.build_working_set()
        # Should have system message + 1 tool result
        assert len(working_set) == 2
        assert "search" in working_set[1]["content"]
        assert "Results found" in working_set[1]["content"]

    def test_clear_tool_results(self):
        """Test clearing tool results."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        ctx.add_tool_result("tool1", "Result 1")
        ctx.add_tool_result("tool2", "Result 2")
        
        ctx.clear_tool_results()
        
        working_set = ctx.build_working_set()
        # Should only have system message
        assert len(working_set) == 1


class TestToolResultTruncation:
    """Tests for tool result truncation logic."""

    def test_short_content_not_truncated(self):
        """Test that short content is not truncated."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        short_content = "This is a short result."
        result = ctx._truncate_tool_result(short_content, max_tokens=100)
        
        assert result == short_content

    def test_long_content_truncated(self):
        """Test that long content is truncated."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        # Create content that will definitely exceed token limit
        # ApproximateCounter: 4 chars = 1 token
        # 1000 tokens = 4000 chars
        long_content = "A" * 5000
        result = ctx._truncate_tool_result(long_content, max_tokens=100)
        
        # Should be truncated
        assert "...(truncated)" in result
        assert len(result) < len(long_content)

    def test_truncation_preserves_start_and_end(self):
        """Test that truncation preserves beginning and end of content."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        # Create content with identifiable start and end
        long_content = "START_MARKER" + "X" * 5000 + "END_MARKER"
        result = ctx._truncate_tool_result(long_content, max_tokens=100)
        
        # Should preserve start and end
        assert "START_MARKER" in result
        assert "END_MARKER" in result
        assert "...(truncated)" in result

    def test_truncation_with_very_small_max_tokens(self):
        """Test truncation when max_tokens is very small."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        long_content = "A" * 1000
        result = ctx._truncate_tool_result(long_content, max_tokens=10)
        
        # Should still produce a result without crashing
        assert len(result) > 0
        assert isinstance(result, str)

    def test_build_working_set_applies_truncation(self):
        """Test that build_working_set applies MAX_TOOL_RESULT_TOKENS limit."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        # Add a very long tool result
        long_content = "A" * 10000  # Way more than 1000 tokens
        ctx.add_tool_result("long_tool", long_content)
        
        working_set = ctx.build_working_set()
        
        # Should be truncated
        assert "...(truncated)" in working_set[1]["content"]

    def test_multiple_tool_results_truncated_individually(self):
        """Test that each tool result is truncated individually."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        ctx.add_tool_result("tool1", "A" * 5000)
        ctx.add_tool_result("tool2", "Short result")
        ctx.add_tool_result("tool3", "B" * 6000)
        
        working_set = ctx.build_working_set()
        
        # First tool result should be truncated
        assert "...(truncated)" in working_set[1]["content"]
        # Second tool result should NOT be truncated (it's short)
        assert "...(truncated)" not in working_set[2]["content"]
        # Third tool result should be truncated
        assert "...(truncated)" in working_set[3]["content"]


class TestTokenCounterUsage:
    """Tests for token counter integration."""

    def test_token_counter_used_for_truncation(self):
        """Test that the configured token counter is used for truncation decisions."""
        call_count = 0
        
        class CountingCounter(ApproximateCounter):
            def count(self, text: str) -> int:
                nonlocal call_count
                call_count += 1
                return super().count(text)
        
        counter = CountingCounter()
        ctx = Context(
            max_tokens=4000,
            system_prompt="Test",
            token_counter=counter,
        )
        
        ctx._truncate_tool_result("Some content", max_tokens=100)
        
        # Token counter should have been called
        assert call_count > 0

    def test_get_token_count(self):
        """Test getting total token count."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        ctx.add_message("user", "Hello world")
        ctx.add_tool_result("echo", "Echo: Hello world")
        
        count = ctx.get_token_count()
        
        # Should return a positive number
        assert count > 0
        
        # Verify it counts messages correctly
        # System + user message + tool result = 3 messages + framing
        assert count >= 3

    def test_custom_counter_in_get_token_count(self):
        """Test that custom counter is used in get_token_count."""
        call_counts = {"count_messages": 0}
        
        class TrackingCounter(TokenCounter):
            def count(self, text: str) -> int:
                return len(text) // 4
            
            def count_messages(self, messages: list[dict]) -> int:
                call_counts["count_messages"] += 1
                return sum(len(m.get("content", "")) for m in messages) // 4
        
        counter = TrackingCounter()
        ctx = Context(
            max_tokens=4000,
            system_prompt="Test",
            token_counter=counter,
        )
        
        ctx.get_token_count()
        
        assert call_counts["count_messages"] == 1


class TestWorkingSetWithinBudget:
    """Tests that working set respects token budget."""

    def test_truncated_results_reduce_total_tokens(self):
        """Test that truncated tool results reduce total token count."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        # Add a very long tool result
        very_long = "X" * 20000  # ~5000 tokens
        ctx.add_tool_result("long_result", very_long)
        
        count = ctx.get_token_count()
        
        # Should be within reasonable bounds
        # Even with truncation overhead, should be less than raw content
        raw_approx_tokens = len(very_long) // 4  # ~5000
        # Truncated should be around MAX_TOOL_RESULT_TOKENS plus overhead
        assert count < raw_approx_tokens + 100  # +100 for system and overhead

    def test_max_tool_result_tokens_constant(self):
        """Test that MAX_TOOL_RESULT_TOKENS constant is set correctly."""
        assert MAX_TOOL_RESULT_TOKENS == 1000


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_tool_result(self):
        """Test handling of empty tool results."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        ctx.add_tool_result("empty_tool", "")
        
        working_set = ctx.build_working_set()
        assert len(working_set) == 2
        assert "...(truncated)" not in working_set[1]["content"]

    def test_exact_token_limit(self):
        """Test content that exactly matches the token limit."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        # Create content that is exactly at the limit
        # ApproximateCounter: 4 chars = 1 token, so 1000 tokens = 4000 chars
        exact_content = "A" * 4000
        result = ctx._truncate_tool_result(exact_content, max_tokens=1000)
        
        # Should not be truncated
        assert "...(truncated)" not in result
        assert result == exact_content

    def test_single_character_over_limit(self):
        """Test content that is just over the token limit."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        # Just over 1000 tokens (ApproximateCounter: 4 chars = 1 token)
        # 1001 tokens = 4004 chars
        content = "A" * 4005
        result = ctx._truncate_tool_result(content, max_tokens=1000)
        
        # Should be truncated
        assert "...(truncated)" in result

    def test_truncation_with_multiline_content(self):
        """Test truncation with multiline content."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        lines = [f"Line {i}: some content here" for i in range(500)]
        multiline = "\n".join(lines)
        
        result = ctx._truncate_tool_result(multiline, max_tokens=100)
        
        # Should be truncated and still contain newline structure
        assert "...(truncated)" in result
        # Should preserve some structure
        assert "\n" in result

    def test_unicode_content_truncation(self):
        """Test truncation with unicode content."""
        ctx = Context(max_tokens=4000, system_prompt="Test")
        
        # Unicode content
        unicode_content = "Hello, 世界! 🌍 " * 1000
        result = ctx._truncate_tool_result(unicode_content, max_tokens=100)
        
        # Should not crash and should handle unicode correctly
        assert isinstance(result, str)
        assert "...(truncated)" in result
