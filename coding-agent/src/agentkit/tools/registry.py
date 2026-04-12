"""ToolRegistry — central registry for agent tools."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any, Callable

from agentkit.errors import ToolError
from agentkit.tools.schema import ToolSchema


class ToolRegistry:
    """Registry for @tool-decorated functions."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._schemas: dict[str, ToolSchema] = {}

    def register(self, fn: Callable[..., Any]) -> None:
        """Register a @tool-decorated function."""
        schema: ToolSchema | None = getattr(fn, "_tool_schema", None)
        if schema is None:
            raise ToolError(f"'{getattr(fn, '__name__', fn)}' missing @tool decorator")
        if schema.name in self._tools:
            raise ToolError(f"tool '{schema.name}' already registered")
        self._tools[schema.name] = fn
        self._schemas[schema.name] = schema

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def get(self, name: str) -> Callable[..., Any]:
        """Get a tool function by name. Raises ToolError if not found."""
        if name not in self._tools:
            raise ToolError(f"tool '{name}' not found")
        return self._tools[name]

    def schemas(self) -> list[ToolSchema]:
        """Return schemas for all registered tools."""
        return list(self._schemas.values())

    def retain(self, names: Iterable[str]) -> None:
        keep = set(names)
        unknown = keep - set(self._tools)
        if unknown:
            raise ToolError(
                f"cannot retain unknown tools: {', '.join(sorted(unknown))}"
            )
        self._tools = {n: self._tools[n] for n in self._tools if n in keep}
        self._schemas = {n: self._schemas[n] for n in self._schemas if n in keep}

    def execute(self, name: str, **kwargs: Any) -> Any:
        """Execute a tool synchronously by name."""
        fn = self.get(name)
        return fn(**kwargs)

    async def execute_async(self, name: str, **kwargs: Any) -> Any:
        """Execute a tool by name, awaiting if async."""
        fn = self.get(name)
        result = fn(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
