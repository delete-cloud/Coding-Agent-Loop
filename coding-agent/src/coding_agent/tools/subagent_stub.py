from __future__ import annotations

from agentkit.tools import tool


@tool(
    name="subagent",
    description=(
        "Dispatch a sub-agent to work on a specific sub-task independently. "
        "The sub-agent gets its own context and tool access."
    ),
)
def subagent_dispatch(goal: str, tools: list = None) -> str:
    raise NotImplementedError("subagent tool not yet wired for Pipeline mode")
