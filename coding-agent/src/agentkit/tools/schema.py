"""ToolSchema — describes a tool's interface for LLM function calling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    """Immutable tool description for LLM function calling.

    Attributes:
        name: Tool identifier (used in function calls).
        description: Human-readable description shown to LLM.
        parameters: JSON Schema for the tool's parameters.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
