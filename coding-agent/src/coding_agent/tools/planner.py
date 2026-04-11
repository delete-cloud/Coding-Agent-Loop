"""Planner tools — todo management."""

from __future__ import annotations

import json
from typing import Any, Callable

from agentkit.tools import tool
from coding_agent.core.planner import PlanManager

_planner: PlanManager | None = None


def configure_planner(planner: PlanManager | None) -> None:
    global _planner
    _planner = planner


def register_planner_tools(registry: Any, planner: Any = None) -> None:
    pass


def build_planner_tools(
    planner: PlanManager | None,
) -> tuple[Callable[..., str], Callable[[], str]]:
    @tool(
        name="todo_write",
        description="Write/update the todo list. Replaces the entire list or applies updates.",
    )
    def bound_todo_write(
        tasks: list[dict[str, Any]] | None = None,
        updates: list[dict[str, Any]] | None = None,
    ) -> str:
        if planner is None:
            return json.dumps(
                {"status": "error", "message": "Planner is not configured"}
            )

        try:
            if tasks is not None:
                planner.set_tasks(tasks)
            for update in updates or []:
                task_id = update.get("id")
                if task_id is None:
                    return json.dumps(
                        {"status": "error", "message": "Update missing id"}
                    )
                payload = {k: v for k, v in update.items() if k != "id"}
                planner.update_task(task_id, **payload)
            return json.dumps({"status": "ok", "tasks": planner.to_dict()})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    @tool(name="todo_read", description="Read the current todo list.")
    def bound_todo_read() -> str:
        if planner is None:
            return "No tasks."
        return planner.to_text()

    return bound_todo_write, bound_todo_read


@tool(
    description="Write/update the todo list. Replaces the entire list or applies updates."
)
def todo_write(
    tasks: list[dict[str, Any]] | None = None,
    updates: list[dict[str, Any]] | None = None,
) -> str:
    return build_planner_tools(_planner)[0](tasks=tasks, updates=updates)


@tool(description="Read the current todo list.")
def todo_read() -> str:
    return build_planner_tools(_planner)[1]()
