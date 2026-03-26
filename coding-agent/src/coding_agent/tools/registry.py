"""Tool registry for registering and executing tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from coding_agent.providers.base import ToolSchema


# Type alias for tool handler functions
ToolHandler = Callable[..., Awaitable[str]]


@dataclass
class ToolDef:
    """Tool definition."""
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler


class ToolRegistry:
    """Registry for tools.
    
    Manages tool registration, schema generation, and execution.
    """

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        """Register a tool.
        
        Args:
            name: Tool name (must be unique)
            description: Tool description for LLM
            parameters: JSON schema for parameters
            handler: Async function to execute the tool
        """
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )

    def schemas(self) -> list[ToolSchema]:
        """Get all tool schemas for LLM.
        
        Returns:
            List of ToolSchema objects
        """
        return [
            ToolSchema(
                type="function",
                function={
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            )
            for tool in self._tools.values()
        ]

    def get(self, name: str) -> ToolDef | None:
        """Get a tool definition by name."""
        return self._tools.get(name)

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name.
        
        Args:
            name: Tool name
            arguments: Tool arguments
            
        Returns:
            Tool result as string
            
        Raises:
            ValueError: If tool not found
            Exception: If tool execution fails
        """
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {name}"})

        try:
            result = await tool.handler(**arguments)
            return result
        except Exception as e:
            return json.dumps({"error": f"Tool execution failed: {e}"})

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())


# Import ToolCall from providers.base to avoid duplication
from coding_agent.providers.base import ToolCall
