"""Configuration management for the coding agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Configuration for the coding agent.
    
    Attributes:
        tape_dir: Directory for storing tape files.
        max_tokens: Maximum tokens allowed in context.
        model: Model name to use.
    """
    
    tape_dir: Path
    max_tokens: int = 8000
    model: str = "gpt-4"
    
    def __post_init__(self):
        """Ensure tape_dir exists."""
        self.tape_dir.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def default(cls, tape_dir: str | Path | None = None) -> Config:
        """Create a default configuration.
        
        Args:
            tape_dir: Optional tape directory path. Defaults to ~/.coding_agent/tapes.
            
        Returns:
            Config instance with default values.
        """
        if tape_dir is None:
            tape_dir = Path.home() / ".coding_agent" / "tapes"
        return cls(tape_dir=Path(tape_dir))
