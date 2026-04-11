"""Tool registry for registering and executing tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from coding_agent.providers.base import ToolSchema
from coding_agent.tools.cache import ToolCache


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
    Supports optional result caching for file_read operations.
    """

    def __init__(self, enable_cache: bool = True, cache_size: int = 100, repo_root: Path | str = "."):
        self._tools: dict[str, ToolDef] = {}
        self._repo_root = Path(repo_root).resolve()
        self._cache = ToolCache(max_size=cache_size) if enable_cache else None

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

        # Check cache first (only for file_read)
        if self._cache and name == "file_read":
            cached = self._cache.get(name, arguments, self._repo_root)
            if cached is not None:
                return cached

        try:
            result = await tool.handler(**arguments)
            
            # Cache the result (only file_read is cached)
            if self._cache and name == "file_read":
                self._cache.set(name, arguments, result, self._repo_root)
            
            return result
        except Exception as e:
            return json.dumps({"error": f"Tool execution failed: {e}"})

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())
    
    @property
    def cache_stats(self) -> dict[str, Any] | None:
        """Get cache statistics if caching is enabled."""
        if self._cache:
            return self._cache.stats
        return None
    
    def clear_cache(self) -> None:
        """Clear the tool result cache."""
        if self._cache:
            self._cache.clear()


# Import ToolCall from providers.base to avoid duplication
from coding_agent.providers.base import ToolCall
