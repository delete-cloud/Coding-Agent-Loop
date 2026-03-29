"""Tests for token counting utilities."""

import pytest

from coding_agent.tokens import ApproximateCounter, TiktokenCounter, TokenCounter


class TestTiktokenCounter:
    """Tests for TiktokenCounter."""

    def test_implements_protocol(self):
        """Test that TiktokenCounter implements TokenCounter protocol."""
        counter = TiktokenCounter()
        assert isinstance(counter, TokenCounter)

    def test_empty_string_returns_zero(self):
        """Test that empty string returns 0 tokens."""
        counter = TiktokenCounter()
        assert counter.count("") == 0

    def test_single_character(self):
        """Test counting a single character."""
        counter = TiktokenCounter()
        # Single ASCII character is typically 1 token
        assert counter.count("a") == 1

    def test_known_text_counting(self):
        """Test counting known text with expected token counts."""
        counter = TiktokenCounter()

        # "hello" is typically 1 token
        assert counter.count("hello") == 1

        # Common words
        text = "The quick brown fox"
        count = counter.count(text)
        assert count > 0
        # This phrase is typically 4-5 tokens
        assert 3 <= count <= 6

    def test_long_text(self):
        """Test counting long text."""
        counter = TiktokenCounter()
        text = "This is a longer piece of text that should contain more tokens. " * 10
        count = counter.count(text)
        assert count > 10

    def test_unicode_text(self):
        """Test counting unicode text."""
        counter = TiktokenCounter()
        # Unicode characters may take more tokens
        text = "Hello, 世界! 🌍"
        count = counter.count(text)
        assert count > 0

    def test_count_empty_messages(self):
        """Test counting empty message list."""
        counter = TiktokenCounter()
        assert counter.count_messages([]) == 0

    def test_count_single_message(self):
        """Test counting a single message."""
        counter = TiktokenCounter()
        messages = [{"role": "user", "content": "Hello"}]
        count = counter.count_messages(messages)
        # Should include message framing tokens
        assert count > 0

    def test_count_multiple_messages(self):
        """Test counting multiple messages."""
        counter = TiktokenCounter()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        count = counter.count_messages(messages)
        assert count > 0

        # Sum of individual counts should be less than message count due to framing
        individual_sum = sum(counter.count(m.get("content", "")) for m in messages)
        assert count > individual_sum

    def test_message_without_content(self):
        """Test counting message without content key."""
        counter = TiktokenCounter()
        messages = [{"role": "user"}]
        count = counter.count_messages(messages)
        # Should still count framing tokens
        assert count >= 4

    def test_different_models(self):
        """Test initialization with different model names."""
        # Should work with common models
        for model in ["gpt-4", "gpt-3.5-turbo", "gpt-4o"]:
            counter = TiktokenCounter(model=model)
            assert counter.count("hello") == 1

    def test_unknown_model_fallback(self):
        """Test fallback behavior for unknown models."""
        counter = TiktokenCounter(model="unknown-model-v1")
        # Should fall back to cl100k_base encoding
        assert counter.count("hello") == 1


class TestApproximateCounter:
    """Tests for ApproximateCounter."""

    def test_implements_protocol(self):
        """Test that ApproximateCounter implements TokenCounter protocol."""
        counter = ApproximateCounter()
        assert isinstance(counter, TokenCounter)

    def test_empty_string_returns_zero(self):
        """Test that empty string returns 0 tokens."""
        counter = ApproximateCounter()
        assert counter.count("") == 0

    def test_approximate_counting(self):
        """Test approximate token counting."""
        counter = ApproximateCounter()

        # 4 characters ≈ 1 token
        assert counter.count("abcd") == 1
        assert counter.count("abcdefgh") == 2

    def test_known_text_approximation(self):
        """Test that approximation is reasonable for known text."""
        counter = ApproximateCounter()

        text = "The quick brown fox jumps over the lazy dog"
        approx_count = counter.count(text)
        char_count = len(text)

        # Should be approximately len / 4
        expected = char_count // 4
        assert approx_count == expected

    def test_count_empty_messages(self):
        """Test counting empty message list."""
        counter = ApproximateCounter()
        assert counter.count_messages([]) == 0

    def test_count_single_message(self):
        """Test counting a single message."""
        counter = ApproximateCounter()
        messages = [{"role": "user", "content": "Hello"}]
        count = counter.count_messages(messages)
        # 9 chars (role + content) + 16 framing = 25 chars ≈ 6 tokens
        assert count > 0

    def test_count_multiple_messages(self):
        """Test counting multiple messages."""
        counter = ApproximateCounter()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        count = counter.count_messages(messages)
        assert count > 0

    def test_message_without_content(self):
        """Test counting message without content key."""
        counter = ApproximateCounter()
        messages = [{"role": "user"}]
        count = counter.count_messages(messages)
        # 4 chars (role) + 16 framing = 20 chars ≈ 5 tokens
        assert count == 5


class TestCounterComparison:
    """Tests comparing different counter implementations."""

    def test_empty_text_consistency(self):
        """Test that both counters return 0 for empty text."""
        tiktoken_counter = TiktokenCounter()
        approx_counter = ApproximateCounter()

        assert tiktoken_counter.count("") == approx_counter.count("") == 0

    def test_empty_messages_consistency(self):
        """Test that both counters return 0 for empty messages."""
        tiktoken_counter = TiktokenCounter()
        approx_counter = ApproximateCounter()

        assert tiktoken_counter.count_messages([]) == approx_counter.count_messages([]) == 0

    def test_approximate_is_reasonable(self):
        """Test that approximate counter gives reasonable estimates."""
        tiktoken_counter = TiktokenCounter()
        approx_counter = ApproximateCounter()

        text = "This is a test sentence with some words in it."
        exact = tiktoken_counter.count(text)
        approx = approx_counter.count(text)

        # Approximate should be within 50% of exact for English text
        assert abs(exact - approx) / max(exact, 1) < 0.5 or abs(exact - approx) <= 2


class TestEdgeCases:
    """Tests for edge cases."""

    def test_very_long_text(self):
        """Test counting very long text."""
        tiktoken_counter = TiktokenCounter()
        approx_counter = ApproximateCounter()

        long_text = "word " * 10000

        tiktoken_count = tiktoken_counter.count(long_text)
        approx_count = approx_counter.count(long_text)

        # Both should handle long text without error
        assert tiktoken_count > 0
        assert approx_count > 0

    def test_special_characters(self):
        """Test counting text with special characters."""
        counter = TiktokenCounter()

        special_texts = [
            "\n\n\n",
            "\t\t\t",
            "   ",
            "!@#$%^&*()",
            "<|endoftext|>",
        ]

        for text in special_texts:
            count = counter.count(text)
            assert count >= 0

    def test_whitespace_only(self):
        """Test counting whitespace-only text."""
        counter = TiktokenCounter()

        # Whitespace may or may not be tokenized
        assert counter.count("   ") >= 0
        assert counter.count("\n\t  \n") >= 0
