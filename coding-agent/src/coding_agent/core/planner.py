"""PlanManager: TodoWrite-style task planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass
class Task:
    id: int
    title: str
    status: TaskStatus


_STATUS_ICONS = {
    TaskStatus.TODO: "[ ]",
    TaskStatus.IN_PROGRESS: "[>]",
    TaskStatus.DONE: "[x]",
    TaskStatus.BLOCKED: "[!]",
}


class PlanManager:
    """Manages a task plan. Used by the todo_write/todo_read tools."""

    def __init__(self):
        self.tasks: list[Task] = []

    def set_tasks(self, task_dicts: list[dict[str, Any]]) -> None:
        """Replace the plan with a new list of tasks.

        Args:
            task_dicts: List of dicts with 'title' and 'status' keys.
                        Optional 'id' key (auto-assigned if missing).
        """
        self.tasks = []
        for i, td in enumerate(task_dicts, start=1):
            self.tasks.append(Task(
                id=td.get("id", i),
                title=td["title"],
                status=TaskStatus(td.get("status", "todo")),
            ))

    def update_task(self, task_id: int, **fields: Any) -> None:
        """Update a task by ID.

        Args:
            task_id: ID of the task to update
            **fields: Fields to update (title, status)

        Raises:
            ValueError: If task not found
        """
        for task in self.tasks:
            if task.id == task_id:
                if "title" in fields:
                    task.title = fields["title"]
                if "status" in fields:
                    task.status = TaskStatus(fields["status"])
                return
        raise ValueError(f"Task {task_id} not found")

    def next_task(self) -> Task | None:
        """Get the next actionable task (in_progress first, then todo)."""
        for task in self.tasks:
            if task.status == TaskStatus.IN_PROGRESS:
                return task
        for task in self.tasks:
            if task.status == TaskStatus.TODO:
                return task
        return None

    def to_text(self) -> str:
        """Render plan as human-readable text."""
        if not self.tasks:
            return "No tasks."
        lines = []
        for task in self.tasks:
            icon = _STATUS_ICONS[task.status]
            lines.append(f"{icon} {task.id}. {task.title}")
        return "\n".join(lines)

    def to_dict(self) -> list[dict[str, Any]]:
        """Serialize to list of dicts (for tape storage)."""
        return [
            {"id": t.id, "title": t.title, "status": t.status.value}
            for t in self.tasks
        ]
