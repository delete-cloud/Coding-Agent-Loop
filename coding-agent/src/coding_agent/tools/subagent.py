"""Sub-agent dispatch tool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from coding_agent.agents.subagent import SubAgent
from coding_agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from coding_agent.core.tape import Tape
    from coding_agent.providers.base import ChatProvider
    from coding_agent.wire import WireConsumer


def register_subagent_tool(
    registry: ToolRegistry,
    provider: ChatProvider,
    tape: Tape,
    consumer: WireConsumer,
    max_steps: int = 15,
    max_depth: int = 3,
) -> None:
    """Register the subagent dispatch tool."""

    subagent = SubAgent(
        provider=provider,
        consumer=consumer,
        max_steps=max_steps,
        max_depth=max_depth,
    )

    async def subagent_dispatch(goal: str, tools: list[str] | None = None) -> str:
        """Dispatch a sub-agent to work on a specific sub-task.

        Args:
            goal: Clear description of what the sub-agent should accomplish
            tools: Optional list of tool names to restrict which tools the sub-agent can use.
                   If not provided, the sub-agent has access to all tools.
        """
        # If tools filter is provided, create a filtered registry
        if tools is not None:
            filtered_registry = ToolRegistry()
            for tool_name in tools:
                tool_def = registry.get(tool_name)
                if tool_def is not None:
                    filtered_registry.register(
                        name=tool_def.name,
                        description=tool_def.description,
                        parameters=tool_def.parameters,
                        handler=tool_def.handler,
                    )
            target_registry = filtered_registry
        else:
            target_registry = registry

        result = await subagent.run(
            goal=goal,
            parent_tape=tape,
            tools=target_registry,
        )
        return json.dumps({
            "success": result.success,
            "output": result.output,
            "stop_reason": result.stop_reason,
            "steps_taken": result.steps_taken,
            "entries_count": result.tape_entries,
        })

    registry.register(
        name="subagent",
        description=(
            "Dispatch a sub-agent to work on a specific sub-task independently. "
            "The sub-agent gets its own context and tool access. Use this for: "
            "reading large codebases, running tests in isolation, or any task "
            "that can be done independently. The sub-agent's results are merged "
            "back if successful."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Clear, specific description of the sub-task",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of tool names to restrict which tools the sub-agent can use",
                },
            },
            "required": ["goal"],
        },
        handler=subagent_dispatch,
    )
