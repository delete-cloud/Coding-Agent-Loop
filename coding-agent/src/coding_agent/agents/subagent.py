"""SubAgent: fork tape, run independent loop, merge results.

NOTE: This module uses the old AgentLoop which has been removed.
SubAgent.run() will raise ImportError at runtime. The Pipeline uses
subagent_stub instead. This module is kept only for its SubAgent and
SubAgentResult types which are still referenced by tools/subagent.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from coding_agent.providers.base import ChatProvider
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.wire.protocol import WireMessage

    class WireConsumer(Protocol):
        async def emit(self, msg: WireMessage) -> None: ...
        async def request_approval(self, req: Any) -> Any: ...


@dataclass
class SubAgentResult:
    """Result of a sub-agent execution."""

    success: bool
    output: str
    stop_reason: str
    steps_taken: int = 0
    tape_entries: int = 0


class SubAgent:
    """Dispatches a sub-agent on a forked tape.

    The sub-agent gets its own AgentLoop with a forked tape.
    On success, forked entries are merged back into the parent tape.
    On failure, the fork is discarded.
    """

    def __init__(
        self,
        provider: ChatProvider,
        consumer: WireConsumer,
        max_steps: int = 15,
        max_depth: int = 3,
        doom_threshold: int = 3,
        enable_parallel: bool = True,
        max_parallel: int = 5,
    ):
        self.provider = provider
        self.consumer = consumer
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.doom_threshold = doom_threshold
        self.enable_parallel = enable_parallel
        self.max_parallel = max_parallel

    async def run(
        self,
        goal: str,
        parent_tape: Any,
        tools: ToolRegistry,
        depth: int = 0,
    ) -> SubAgentResult:
        raise NotImplementedError(
            "Old SubAgent.run() requires deleted AgentLoop. "
            "Use Pipeline-based subagent_stub instead."
        )
