"""Token counting utilities for context budget management."""

from __future__ import annotations

import logging
import warnings
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class TokenCounter(Protocol):
    """Protocol for token counting implementations."""

    def count(self, text: str) -> int:
        """Count tokens in a text string.

        Args:
            text: The text to count tokens for.

        Returns:
            Number of tokens in the text.
        """
        ...

    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens in a list of chat messages.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.

        Returns:
            Total number of tokens in all messages.
        """
        ...


class TiktokenCounter:
    """Exact token counting using tiktoken for OpenAI models.

    Falls back to ApproximateCounter if tiktoken is not available.
    """

    _fallback_counter: ApproximateCounter | None = None
    _tiktoken_available: bool | None = None

    def __init__(self, model: str = "gpt-4"):
        """Initialize the counter with a specific model.

        Args:
            model: The model name to use for tokenization.
        """
        self.model = model
        self._encoding: Any | None = None
        self._check_tiktoken()

    def _check_tiktoken(self) -> bool:
        """Check if tiktoken is available, setup fallback if not."""
        if TiktokenCounter._tiktoken_available is not None:
            # If tiktoken was previously determined to be unavailable, use fallback
            if not TiktokenCounter._tiktoken_available:
                return False
            # Otherwise, initialize encoding for this instance
            try:
                import tiktoken
                try:
                    self._encoding = tiktoken.encoding_for_model(self.model)
                except KeyError:
                    logger.debug(f"Model {self.model} not found in tiktoken, using cl100k_base")
                    self._encoding = tiktoken.get_encoding("cl100k_base")
            except ImportError:
                pass  # Should not happen since _tiktoken_available is True
            return TiktokenCounter._tiktoken_available

        try:
            import tiktoken

            # Try to get encoding for the model
            try:
                self._encoding = tiktoken.encoding_for_model(self.model)
            except KeyError:
                # Model not found, use default
                logger.debug(f"Model {self.model} not found in tiktoken, using cl100k_base")
                self._encoding = tiktoken.get_encoding("cl100k_base")

            TiktokenCounter._tiktoken_available = True
            return True

        except ImportError:
            TiktokenCounter._tiktoken_available = False
            TiktokenCounter._fallback_counter = ApproximateCounter()

            warnings.warn(
                "tiktoken is not installed. Using ApproximateCounter for token counting. "
                "Install tiktoken for exact counts: pip install tiktoken",
                UserWarning,
                stacklevel=3,
            )
            logger.warning(
                "tiktoken not available, falling back to ApproximateCounter. "
                "Token counts will be approximate (±20%)."
            )
            return False

    def count(self, text: str) -> int:
        """Count tokens in a text string.

        Args:
            text: The text to count tokens for.

        Returns:
            Number of tokens in the text.
        """
        if not text:
            return 0

        if TiktokenCounter._tiktoken_available:
            # Disable special token checks to handle any input text safely
            return len(self._encoding.encode(text, disallowed_special=()))
        else:
            return TiktokenCounter._fallback_counter.count(text)

    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens in a list of chat messages.

        Uses OpenAI's message token counting convention:
        - 4 tokens for message framing per message
        - 2 tokens for assistant message priming

        Args:
            messages: List of message dicts with 'role' and 'content' keys.

        Returns:
            Total number of tokens in all messages.
        """
        if not messages:
            return 0

        if TiktokenCounter._tiktoken_available:
            return self._count_messages_exact(messages)
        else:
            return TiktokenCounter._fallback_counter.count_messages(messages)

    def _count_messages_exact(self, messages: list[dict]) -> int:
        """Exact message counting using tiktoken."""
        num_tokens = 0
        for message in messages:
            # Every message follows <|start|>{role/name}\n{content}<|end|>\n
            num_tokens += 4  # Base tokens for message framing

            content = message.get("content", "")
            if content:
                num_tokens += self.count(content)

            # Role token count approximation
            role = message.get("role", "")
            if role:
                num_tokens += self.count(role)

        # Every reply is primed with <|start|>assistant<|message|>
        num_tokens += 2

        return num_tokens


class ApproximateCounter:
    """Fallback counter: 1 token ≈ 4 characters."""

    # Average characters per token for English text
    CHARS_PER_TOKEN = 4

    def count(self, text: str) -> int:
        """Approximately count tokens in a text string.

        Uses the heuristic: 1 token ≈ 4 characters.

        Args:
            text: The text to count tokens for.

        Returns:
            Estimated number of tokens in the text.
        """
        if not text:
            return 0
        return len(text) // self.CHARS_PER_TOKEN

    def count_messages(self, messages: list[dict]) -> int:
        """Approximately count tokens in a list of chat messages.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.

        Returns:
            Estimated total number of tokens in all messages.
        """
        if not messages:
            return 0

        total_chars = 0
        for message in messages:
            # Count content
            content = message.get("content", "")
            total_chars += len(content)

            # Count role
            role = message.get("role", "")
            total_chars += len(role)

        # Add overhead for message framing (~4 tokens = ~16 chars per message)
        total_chars += len(messages) * 16

        return total_chars // self.CHARS_PER_TOKEN


def create_token_counter(model: str = "gpt-4") -> TokenCounter:
    """Factory function to create the best available token counter.

    Tries TiktokenCounter first, falls back to ApproximateCounter.

    Args:
        model: The model name to use for tokenization.

    Returns:
        A TokenCounter instance.
    """
    try:
        import tiktoken

        return TiktokenCounter(model)
    except ImportError:
        logger.warning("tiktoken not available, using ApproximateCounter")
        return ApproximateCounter()
