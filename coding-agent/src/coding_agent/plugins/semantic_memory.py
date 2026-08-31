"""Renderer-only semantic-memory context plugin."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentkit.plugin import PluginCapability
from coding_agent.topics.semantic_grounding import SemanticMemoryGroundingInput


class SemanticMemoryPlugin:
    """Render a host-owned frozen semantic grounding input."""

    state_key = "semantic_memory"
    capabilities = frozenset({PluginCapability.PENDING_FACT})

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {"build_context": self.build_context}

    async def build_context(
        self, *, input: SemanticMemoryGroundingInput
    ) -> list[dict[str, object]]:
        if not isinstance(input, SemanticMemoryGroundingInput):
            raise TypeError("input must be SemanticMemoryGroundingInput")
        return [
            {"role": message.role, "content": message.content}
            for message in input.messages
        ]
