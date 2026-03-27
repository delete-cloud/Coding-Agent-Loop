"""Base types for context summarization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Summary:
    """Summary result.
    
    Attributes:
        content: The summary text content
        original_tokens: Estimated tokens in original messages
        summary_tokens: Estimated tokens in summary
        key_points: List of extracted key points
    """
    content: str
    original_tokens: int
    summary_tokens: int
    key_points: list[str]


class Summarizer(Protocol):
    """Protocol for context summarizers."""
    
    async def summarize(
        self,
        messages: list[dict],
        max_tokens: int = 500,
    ) -> Summary:
        """Summarize a list of messages.
        
        Args:
            messages: List of message dicts with 'role' and 'content' keys
            max_tokens: Maximum tokens for the summary
            
        Returns:
            Summary object containing the summarized content
        """
        ...
