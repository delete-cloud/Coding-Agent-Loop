"""Planner tools: todo_write and todo_read."""

from __future__ import annotations

import json
from typing import Any

from coding_agent.core.planner import PlanManager
from coding_agent.tools.registry import ToolRegistry

# Module-level singleton PlanManager for use when not injected
_plan_manager: PlanManager | None = None


def _get_plan_manager() -> PlanManager:
    """Get or create the module-level PlanManager singleton."""
    global _plan_manager
    if _plan_manager is None:
        _plan_manager = PlanManager()
    return _plan_manager


def register_planner_tools(
    registry: ToolRegistry,
    planner: PlanManager | None = None,
) -> None:
    """Register todo_write and todo_read tools.
    
    Args:
        registry: The tool registry to register tools with
        planner: Optional PlanManager instance. If not provided, uses the
                 module-level singleton.
    """
    # Use provided planner or the singleton
    plan_manager = planner if planner is not None else _get_plan_manager()

    async def todo_write(
        tasks: list[dict[str, Any]] | None = None,
        updates: list[dict[str, Any]] | None = None,
    ) -> str:
        """Create or update the task plan.

        Args:
            tasks: Full replacement task list (each: {title, status})
            updates: Incremental updates (each: {id, status?, title?})
        """
        try:
            if tasks is not None:
                plan_manager.set_tasks(tasks)

            if updates is not None:
                for update in updates:
                    task_id = update.pop("id")
                    plan_manager.update_task(task_id, **update)

            return json.dumps({
                "status": "ok",
                "plan": plan_manager.to_text(),
                "task_count": len(plan_manager.tasks),
            })
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    async def todo_read() -> str:
        """Read the current task plan."""
        return plan_manager.to_text()

    registry.register(
        name="todo_write",
        description=(
            "Create or update the task plan. Call with 'tasks' to set the full plan, "
            "or 'updates' to modify specific tasks. Each task has: title, status "
            "(todo/in_progress/done/blocked). Always create a plan before starting work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "Full task list (replaces current plan)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["todo", "in_progress", "done", "blocked"],
                            },
                        },
                        "required": ["title", "status"],
                    },
                },
                "updates": {
                    "type": "array",
                    "description": "Incremental updates to existing tasks",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "description": "Task ID to update"},
                            "status": {"type": "string", "enum": ["todo", "in_progress", "done", "blocked"]},
                            "title": {"type": "string"},
                        },
                        "required": ["id"],
                    },
                },
            },
        },
        handler=todo_write,
    )

    registry.register(
        name="todo_read",
        description="Read the current task plan to see progress and next steps.",
        parameters={"type": "object", "properties": {}},
        handler=todo_read,
    )
