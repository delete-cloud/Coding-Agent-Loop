"""Tests for PlanManager."""

from __future__ import annotations

import pytest

from coding_agent.core.planner import PlanManager, Task, TaskStatus


class TestTaskCreation:
    def test_create_task(self):
        t = Task(id=1, title="Fix bug", status=TaskStatus.TODO)
        assert t.id == 1
        assert t.title == "Fix bug"
        assert t.status == TaskStatus.TODO

    def test_task_status_values(self):
        assert TaskStatus.TODO == "todo"
        assert TaskStatus.IN_PROGRESS == "in_progress"
        assert TaskStatus.DONE == "done"
        assert TaskStatus.BLOCKED == "blocked"


class TestPlanManager:
    def test_empty_plan(self):
        pm = PlanManager()
        assert pm.tasks == []
        assert pm.to_text() == "No tasks."

    def test_set_plan_from_list(self):
        pm = PlanManager()
        pm.set_tasks(
            [
                {"title": "Read code", "status": "todo"},
                {"title": "Write tests", "status": "todo"},
            ]
        )
        assert len(pm.tasks) == 2
        assert pm.tasks[0].id == 1
        assert pm.tasks[0].title == "Read code"
        assert pm.tasks[1].id == 2

    def test_set_plan_replaces_existing(self):
        pm = PlanManager()
        pm.set_tasks([{"title": "Old task", "status": "todo"}])
        pm.set_tasks([{"title": "New task", "status": "todo"}])
        assert len(pm.tasks) == 1
        assert pm.tasks[0].title == "New task"

    def test_update_task_status(self):
        pm = PlanManager()
        pm.set_tasks([{"title": "Do thing", "status": "todo"}])
        pm.update_task(1, status="in_progress")
        assert pm.tasks[0].status == TaskStatus.IN_PROGRESS

    def test_update_task_title(self):
        pm = PlanManager()
        pm.set_tasks([{"title": "Old title", "status": "todo"}])
        pm.update_task(1, title="New title")
        assert pm.tasks[0].title == "New title"

    def test_update_nonexistent_task_raises(self):
        pm = PlanManager()
        pm.set_tasks([{"title": "Task", "status": "todo"}])
        with pytest.raises(ValueError, match="Task 99 not found"):
            pm.update_task(99, status="done")

    def test_to_text_formatting(self):
        pm = PlanManager()
        pm.set_tasks(
            [
                {"title": "Read code", "status": "done"},
                {"title": "Write tests", "status": "in_progress"},
                {"title": "Implement", "status": "todo"},
                {"title": "Waiting on review", "status": "blocked"},
            ]
        )
        text = pm.to_text()
        assert "[x] 1. Read code" in text
        assert "[>] 2. Write tests" in text
        assert "[ ] 3. Implement" in text
        assert "[!] 4. Waiting on review" in text

    def test_to_dict_roundtrip(self):
        pm = PlanManager()
        pm.set_tasks(
            [
                {"title": "Read code", "status": "done"},
                {"title": "Write tests", "status": "todo"},
            ]
        )
        data = pm.to_dict()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["title"] == "Read code"
        assert data[0]["status"] == "done"

        pm2 = PlanManager()
        pm2.set_tasks(data)
        assert len(pm2.tasks) == 2
        assert pm2.tasks[0].title == "Read code"
        assert pm2.tasks[0].status == TaskStatus.DONE

    def test_next_task(self):
        pm = PlanManager()
        pm.set_tasks(
            [
                {"title": "Done task", "status": "done"},
                {"title": "Current task", "status": "in_progress"},
                {"title": "Next task", "status": "todo"},
            ]
        )
        next_task = pm.next_task()
        assert next_task is not None
        assert next_task.title == "Current task"

    def test_next_task_skips_done(self):
        pm = PlanManager()
        pm.set_tasks(
            [
                {"title": "Done", "status": "done"},
                {"title": "Also done", "status": "done"},
                {"title": "Todo", "status": "todo"},
            ]
        )
        next_task = pm.next_task()
        assert next_task is not None
        assert next_task.title == "Todo"

    def test_next_task_none_when_all_done(self):
        pm = PlanManager()
        pm.set_tasks([{"title": "Done", "status": "done"}])
        assert pm.next_task() is None

    def test_next_task_none_when_empty(self):
        pm = PlanManager()
        assert pm.next_task() is None
