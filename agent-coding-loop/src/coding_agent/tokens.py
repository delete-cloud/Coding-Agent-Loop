"""Token counting utilities for context budget management."""

import threading
import warnings
from typing import Any, ClassVar, Protocol, runtime_checkable


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
    
    This class uses the tiktoken library for accurate token counting.
    It includes thread-safe initialization of the tiktoken encoding.
    
    Example:
        ```python
        counter = TiktokenCounter(model="gpt-4")
        token_count = counter.count("Hello, world!")
        ```
    """
    
    # Class-level state for tiktoken availability (shared across instances)
    _encoding: ClassVar[Any | None] = None
    _fallback_counter: ClassVar[ApproximateCounter | None] = None
    _tiktoken_available: ClassVar[bool | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, model: str = "gpt-4"):
        """Initialize the counter with a specific model.

        Args:
            model: The model name to use for tokenization.
        """
        self.model = model
        self.encoding = None
        self._initialize_encoding()

    def _initialize_encoding(self) -> None:
        """Initialize the tiktoken encoding (thread-safe).
        
        This method ensures that tiktoken is initialized only once
        across all instances, using a class-level lock for thread safety.
        """
        # Fast path: already initialized
        if TiktokenCounter._tiktoken_available is not None:
            if TiktokenCounter._tiktoken_available:
                self.encoding = TiktokenCounter._encoding
            else:
                self.encoding = TiktokenCounter._fallback_counter
            return
        
        # Slow path: need to initialize
        with TiktokenCounter._lock:
            # Double-check after acquiring lock
            if TiktokenCounter._tiktoken_available is not None:
                if TiktokenCounter._tiktoken_available:
                    self.encoding = TiktokenCounter._encoding
                else:
                    self.encoding = TiktokenCounter._fallback_counter
                return
            
            try:
                import tiktoken
                
                try:
                    TiktokenCounter._encoding = tiktoken.encoding_for_model(self.model)
                except KeyError:
                    # Fall back to cl100k_base for unknown models
                    TiktokenCounter._encoding = tiktoken.get_encoding("cl100k_base")
                
                TiktokenCounter._tiktoken_available = True
                self.encoding = TiktokenCounter._encoding
                
            except ImportError:
                TiktokenCounter._tiktoken_available = False
                TiktokenCounter._fallback_counter = ApproximateCounter()
                self.encoding = TiktokenCounter._fallback_counter
                
                warnings.warn(
                    "tiktoken not installed. Using approximate token counting. "
                    "Install tiktoken for accurate counts: pip install tiktoken",
                    UserWarning,
                    stacklevel=3,
                )

    def count(self, text: str) -> int:
        """Count tokens in a text string.

        Args:
            text: The text to count tokens for.

        Returns:
            Number of tokens in the text.
        """
        if not text:
            return 0
        
        # If using fallback counter, delegate to it
        if isinstance(self.encoding, ApproximateCounter):
            return self.encoding.count(text)
        
        # Disable special token checks to handle any input text safely
        return len(self.encoding.encode(text, disallowed_special=()))

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
        
        # If using fallback counter, delegate to it
        if isinstance(self.encoding, ApproximateCounter):
            return self.encoding.count_messages(messages)

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
