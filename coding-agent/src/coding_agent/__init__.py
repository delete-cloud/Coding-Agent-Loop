"""Interactive coding agent with tape-based context."""

from __future__ import annotations

from typing import TYPE_CHECKING

# TYPE_CHECKING imports - only imported during type checking, not at runtime
if TYPE_CHECKING:
    from coding_agent.kb import KB
    from coding_agent.tokens import TokenCounter


def get_kb(*args, **kwargs):
    """Lazy import and create KB instance.
    
    This function delays the import of heavy dependencies (lancedb, numpy)
    until they are actually needed.
    
    Args:
        *args: Positional arguments to pass to KB constructor
        **kwargs: Keyword arguments to pass to KB constructor
        
    Returns:
        KB instance
    """
    from coding_agent.kb import KB
    return KB(*args, **kwargs)


def get_token_counter(*args, **kwargs):
    """Lazy import and create TokenCounter instance.
    
    This function delays the import of tiktoken until it is actually needed.
    
    Args:
        *args: Positional arguments to pass to TokenCounter constructor
        **kwargs: Keyword arguments to pass to TokenCounter constructor
        
    Returns:
        TokenCounter instance
    """
    from coding_agent.tokens import TokenCounter
    return TokenCounter(*args, **kwargs)
