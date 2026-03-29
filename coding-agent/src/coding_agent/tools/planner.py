"""Planner tools — todo management."""

from __future__ import annotations

import json
from typing import Any

from agentkit.tools import tool


def register_planner_tools(registry: Any, planner: Any = None) -> None:
    pass


_todos: list[dict] = []


@tool(description="Write/update the todo list. Replaces the entire list.")
def todo_write(todos: str) -> str:
    global _todos
    try:
        _todos = json.loads(todos)
        return f"Updated {len(_todos)} todos"
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"


@tool(description="Read the current todo list.")
def todo_read() -> str:
    if not _todos:
        return "No todos."
    return json.dumps(_todos, indent=2)
