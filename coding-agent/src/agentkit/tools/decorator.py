"""@tool decorator — marks functions as agent tools and generates schemas."""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, TypeVar, overload

from agentkit.tools.schema import ToolSchema

F = TypeVar("F", bound=Callable[..., Any])

_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _extract_parameters(fn: Callable[..., Any]) -> dict[str, Any]:
    """Extract JSON Schema parameters from function signature + annotations."""
    sig = inspect.signature(fn)
    hints = {k: v for k, v in inspect.get_annotations(fn).items() if k != "return"}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        prop: dict[str, Any] = {}
        if name in hints:
            hint = hints[name]
            json_type = _TYPE_MAP.get(hint, "string")
            prop["type"] = json_type
        else:
            prop["type"] = "string"

        properties[name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


@overload
def tool(fn: F) -> F: ...
@overload
def tool(
    fn: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable[[F], F]: ...


def tool(
    fn: F | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> F | Callable[[F], F]:
    """Decorate a function as an agent tool.

    Can be used bare (@tool) or with arguments (@tool(name="x")).
    Attaches a ToolSchema as fn._tool_schema.
    """

    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or (func.__doc__ or "").strip()
        params = _extract_parameters(func)

        schema = ToolSchema(
            name=tool_name,
            description=tool_desc,
            parameters=params,
        )

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await func(*args, **kwargs)

            async_wrapper._tool_schema = schema  # type: ignore[attr-defined]
            return async_wrapper  # type: ignore[return-value]

        wrapper._tool_schema = schema  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator
