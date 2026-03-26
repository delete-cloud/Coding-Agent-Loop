"""SubAgent: fork tape, run independent loop, merge results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.core.tape import Tape

if TYPE_CHECKING:
    from coding_agent.providers.base import ChatProvider
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.wire import WireConsumer


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
    ):
        self.provider = provider
        self.consumer = consumer
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.doom_threshold = doom_threshold

    async def run(
        self,
        goal: str,
        parent_tape: Tape,
        tools: ToolRegistry,
        depth: int = 0,
    ) -> SubAgentResult:
        """Run a sub-agent on a forked tape.

        Args:
            goal: The sub-agent's task description
            parent_tape: Parent tape to fork from
            tools: Tool registry (can be restricted)
            depth: Current nesting depth

        Returns:
            SubAgentResult with success flag and output
        """
        # Depth 0 = main agent, depth 1+ = sub-agents
        # max_depth is the maximum allowed nesting level
        if depth > self.max_depth:
            return SubAgentResult(
                success=False,
                output=f"Max sub-agent depth ({self.max_depth}) exceeded at depth {depth}",
                stop_reason="depth_limit",
            )

        # Fork tape for isolated execution
        forked_tape = parent_tape.fork()
        forked_tape.handoff("subagent_start", {"goal": goal, "depth": depth})

        # Create context for sub-agent
        context = Context(
            max_tokens=self.provider.max_context_size,
            system_prompt=(
                f"You are a sub-agent working on a specific task. "
                f"Your goal: {goal}\n"
                f"Focus only on this goal. Be concise."
            ),
        )

        # Run sub-agent loop
        loop = AgentLoop(
            provider=self.provider,
            tools=tools,
            tape=forked_tape,
            context=context,
            consumer=self.consumer,
            max_steps=self.max_steps,
            doom_threshold=self.doom_threshold,
        )

        outcome = await loop.run_turn(goal)

        # Success: only when agent completes normally without hitting limits
        # Failure reasons: max_steps_reached, doom_loop, error
        success = outcome.stop_reason not in ("max_steps_reached", "doom_loop", "error")

        if success:
            # Merge forked entries back into parent tape
            parent_tape.merge(forked_tape)

        return SubAgentResult(
            success=success,
            output=outcome.final_message or "",
            stop_reason=outcome.stop_reason,
            steps_taken=outcome.steps_taken,
            tape_entries=len(forked_tape.entries()),
        )
