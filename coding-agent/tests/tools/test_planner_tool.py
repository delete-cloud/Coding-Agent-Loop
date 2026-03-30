"""Tests for planner tools (todo_write, todo_read)."""

from __future__ import annotations

import json

import pytest

from coding_agent.core.planner import PlanManager
from coding_agent.tools.planner import build_planner_tools


@pytest.fixture
def planner_tools():
    planner = PlanManager()
    todo_write, todo_read = build_planner_tools(planner)
    return todo_write, todo_read, planner


class TestTodoWrite:
    def test_create_plan(self, planner_tools):
        todo_write, _, planner = planner_tools
        result = todo_write(
            tasks=[
                {"title": "Read the code", "status": "todo"},
                {"title": "Write tests", "status": "todo"},
            ]
        )
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert len(planner.tasks) == 2
        assert planner.tasks[0].title == "Read the code"

    def test_update_task_status(self, planner_tools):
        todo_write, _, planner = planner_tools
        todo_write(tasks=[{"title": "Do thing", "status": "todo"}])
        result = todo_write(updates=[{"id": 1, "status": "done"}])
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert planner.tasks[0].status.value == "done"

    def test_update_nonexistent_returns_error(self, planner_tools):
        todo_write, _, planner = planner_tools
        todo_write(tasks=[{"title": "Task", "status": "todo"}])
        result = todo_write(updates=[{"id": 99, "status": "done"}])
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    def test_write_and_update_in_one_call(self, planner_tools):
        todo_write, _, planner = planner_tools
        result = todo_write(
            tasks=[
                {"title": "Task A", "status": "todo"},
                {"title": "Task B", "status": "todo"},
            ],
            updates=[{"id": 1, "status": "in_progress"}],
        )
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert planner.tasks[0].status.value == "in_progress"


class TestTodoRead:
    def test_read_empty_plan(self, planner_tools):
        _, todo_read, planner = planner_tools
        result = todo_read()
        assert "No tasks" in result

    def test_read_populated_plan(self, planner_tools):
        _, todo_read, planner = planner_tools
        planner.set_tasks(
            [
                {"title": "Read code", "status": "done"},
                {"title": "Write tests", "status": "todo"},
            ]
        )
        result = todo_read()
        assert "[x] 1. Read code" in result
        assert "[ ] 2. Write tests" in result

    def test_tool_schemas_registered(self, planner_tools):
        todo_write, todo_read, _ = planner_tools
        assert hasattr(todo_write, "_tool_schema")
        assert todo_write._tool_schema.name == "todo_write"
        assert hasattr(todo_read, "_tool_schema")
        assert todo_read._tool_schema.name == "todo_read"
