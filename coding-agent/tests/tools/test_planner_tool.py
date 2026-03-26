"""Tests for planner tools (todo_write, todo_read)."""

from __future__ import annotations

import json

import pytest

from coding_agent.core.planner import PlanManager
from coding_agent.tools.planner import register_planner_tools
from coding_agent.tools.registry import ToolRegistry


@pytest.fixture
def registry_with_planner():
    registry = ToolRegistry()
    planner = PlanManager()
    register_planner_tools(registry, planner)
    return registry, planner


class TestTodoWrite:
    @pytest.mark.asyncio
    async def test_create_plan(self, registry_with_planner):
        registry, planner = registry_with_planner
        result = await registry.execute("todo_write", {
            "tasks": [
                {"title": "Read the code", "status": "todo"},
                {"title": "Write tests", "status": "todo"},
            ]
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert len(planner.tasks) == 2
        assert planner.tasks[0].title == "Read the code"

    @pytest.mark.asyncio
    async def test_update_task_status(self, registry_with_planner):
        registry, planner = registry_with_planner
        # First create a plan
        await registry.execute("todo_write", {
            "tasks": [{"title": "Do thing", "status": "todo"}]
        })
        # Then update
        result = await registry.execute("todo_write", {
            "updates": [{"id": 1, "status": "done"}]
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert planner.tasks[0].status.value == "done"

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_error(self, registry_with_planner):
        registry, planner = registry_with_planner
        await registry.execute("todo_write", {
            "tasks": [{"title": "Task", "status": "todo"}]
        })
        result = await registry.execute("todo_write", {
            "updates": [{"id": 99, "status": "done"}]
        })
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    @pytest.mark.asyncio
    async def test_write_and_update_in_one_call(self, registry_with_planner):
        """If both tasks and updates provided, tasks replaces plan, then updates apply."""
        registry, planner = registry_with_planner
        result = await registry.execute("todo_write", {
            "tasks": [
                {"title": "Task A", "status": "todo"},
                {"title": "Task B", "status": "todo"},
            ],
            "updates": [{"id": 1, "status": "in_progress"}],
        })
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert planner.tasks[0].status.value == "in_progress"


class TestTodoRead:
    @pytest.mark.asyncio
    async def test_read_empty_plan(self, registry_with_planner):
        registry, planner = registry_with_planner
        result = await registry.execute("todo_read", {})
        assert "No tasks" in result

    @pytest.mark.asyncio
    async def test_read_populated_plan(self, registry_with_planner):
        registry, planner = registry_with_planner
        planner.set_tasks([
            {"title": "Read code", "status": "done"},
            {"title": "Write tests", "status": "todo"},
        ])
        result = await registry.execute("todo_read", {})
        assert "[x] 1. Read code" in result
        assert "[ ] 2. Write tests" in result

    @pytest.mark.asyncio
    async def test_tool_schemas_registered(self, registry_with_planner):
        registry, planner = registry_with_planner
        names = registry.list_tools()
        assert "todo_write" in names
        assert "todo_read" in names
