# P1: Planning + Sub-agents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add task planning (TodoWrite), sub-agent dispatch with fork/merge tape, and Anthropic provider support.

**Architecture:** The planner is a `todo_write` tool backed by a `PlanManager` that stores a task list in the tape. Sub-agents fork the tape, run an independent `AgentLoop`, and merge results back. The Anthropic provider translates between internal OpenAI-format messages and Anthropic's content block API.

**Tech Stack:** Python 3.12+, uv, asyncio, anthropic SDK, pytest

**Spec:** `docs/superpowers/specs/2026-03-26-python-coding-agent-design.md` (Sections 4.1, 4.10, 10, 14-P1)

---

## File Map

```
coding-agent/
  pyproject.toml                           # Add anthropic dependency
  src/coding_agent/
    __main__.py                            # Register planner + subagent tools, provider selection
    core/
      planner.py                           # PlanManager: task plan CRUD, plan state
    providers/
      anthropic.py                         # Anthropic native provider
    tools/
      planner.py                           # register_planner_tools (todo_write, todo_read)
      subagent.py                          # register_subagent_tool (subagent dispatch)
    agents/
      __init__.py
      subagent.py                          # SubAgent: fork tape → run loop → merge
  tests/
    core/
      test_planner.py
    providers/
      test_anthropic.py
    tools/
      test_planner_tool.py
      test_subagent_tool.py
    agents/
      __init__.py
      test_subagent.py
```

---

## Task 1: Add Anthropic SDK Dependency

**Files:**
- Modify: `coding-agent/pyproject.toml`

- [ ] **Step 1: Add anthropic to dependencies**

`coding-agent/pyproject.toml` — add `"anthropic>=0.40.0"` to the dependencies list:

```toml
[project]
name = "coding-agent"
version = "0.1.0"
description = "Interactive coding agent with tape-based context"
requires-python = ">=3.12"
dependencies = [
    "openai>=1.50.0",
    "anthropic>=0.40.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "structlog>=24.0.0",
    "click>=8.0.0",
]
```

- [ ] **Step 2: Install updated dependencies**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv sync --all-extras
```

Expected: installs `anthropic` package successfully

- [ ] **Step 3: Commit**

```bash
git add coding-agent/pyproject.toml
git commit -m "chore(p1): add anthropic SDK dependency"
```

---

## Task 2: Anthropic Provider

**Files:**
- Create: `coding-agent/src/coding_agent/providers/anthropic.py`
- Test: `coding-agent/tests/providers/test_anthropic.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/providers/test_anthropic.py`:

```python
"""Tests for Anthropic provider."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coding_agent.providers.anthropic import AnthropicProvider
from coding_agent.providers.base import StreamEvent, ToolCall, ToolSchema


class TestAnthropicProviderInit:
    def test_init_basic(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        assert p.model_name == "claude-sonnet-4-20250514"
        assert p.max_context_size == 200000

    def test_init_custom_model(self):
        p = AnthropicProvider(model="claude-haiku-4-5-20251001", api_key="sk-test")
        assert p.model_name == "claude-haiku-4-5-20251001"
        assert p.max_context_size == 200000

    def test_init_unknown_model_default_context(self):
        p = AnthropicProvider(model="future-model", api_key="sk-test")
        assert p.max_context_size == 200000


class TestMessageConversion:
    """Test conversion from OpenAI-format messages to Anthropic format."""

    def test_convert_user_message(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, converted = p._convert_messages(messages)
        assert system == "You are helpful."
        assert len(converted) == 1
        assert converted[0] == {"role": "user", "content": "Hello"}

    def test_convert_multiple_system_messages_concatenated(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        messages = [
            {"role": "system", "content": "Rule 1."},
            {"role": "system", "content": "Rule 2."},
            {"role": "user", "content": "Hi"},
        ]
        system, converted = p._convert_messages(messages)
        assert system == "Rule 1.\n\nRule 2."
        assert len(converted) == 1

    def test_convert_assistant_with_tool_calls(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Read foo.py"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "foo.py"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "file contents here",
            },
        ]
        system, converted = p._convert_messages(messages)
        assert system == "sys"
        assert len(converted) == 3  # user, assistant, user(tool_result)

        # Assistant message should have tool_use content block
        assistant_msg = converted[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"][0]["type"] == "tool_use"
        assert assistant_msg["content"][0]["id"] == "call_1"
        assert assistant_msg["content"][0]["name"] == "file_read"

        # Tool result should be user message with tool_result content block
        tool_msg = converted[2]
        assert tool_msg["role"] == "user"
        assert tool_msg["content"][0]["type"] == "tool_result"
        assert tool_msg["content"][0]["tool_use_id"] == "call_1"

    def test_convert_tool_schemas(self):
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")
        schemas = [
            ToolSchema(
                type="function",
                function={
                    "name": "bash",
                    "description": "Run a command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            )
        ]
        result = p._convert_tools(schemas)
        assert len(result) == 1
        assert result[0]["name"] == "bash"
        assert result[0]["description"] == "Run a command"
        assert result[0]["input_schema"]["type"] == "object"


class TestAnthropicStreaming:
    @pytest.mark.asyncio
    async def test_stream_text_response(self):
        """Test text-only response streaming."""
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        # Mock the Anthropic streaming events
        mock_events = [
            MagicMock(type="content_block_start", index=0, content_block=MagicMock(type="text", text="")),
            MagicMock(type="content_block_delta", index=0, delta=MagicMock(type="text_delta", text="Hello")),
            MagicMock(type="content_block_delta", index=0, delta=MagicMock(type="text_delta", text=" world")),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_stop"),
        ]

        async def mock_stream_context(*args, **kwargs):
            ctx = AsyncMock()
            async def aiter_events():
                for e in mock_events:
                    yield e
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.__aiter__ = aiter_events().__aiter__
            return ctx

        with patch.object(p._client.messages, "stream", side_effect=mock_stream_context):
            events = []
            async for event in p.stream(
                messages=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hi"},
                ]
            ):
                events.append(event)

        deltas = [e for e in events if e.type == "delta"]
        assert len(deltas) == 2
        assert deltas[0].text == "Hello"
        assert deltas[1].text == " world"
        assert events[-1].type == "done"

    @pytest.mark.asyncio
    async def test_stream_tool_use_response(self):
        """Test tool use response streaming."""
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="sk-test")

        mock_events = [
            MagicMock(
                type="content_block_start",
                index=0,
                content_block=MagicMock(type="tool_use", id="toolu_1", name="bash"),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="input_json_delta", partial_json='{"command":'),
            ),
            MagicMock(
                type="content_block_delta",
                index=0,
                delta=MagicMock(type="input_json_delta", partial_json=' "ls"}'),
            ),
            MagicMock(type="content_block_stop", index=0),
            MagicMock(type="message_stop"),
        ]

        async def mock_stream_context(*args, **kwargs):
            ctx = AsyncMock()
            async def aiter_events():
                for e in mock_events:
                    yield e
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.__aiter__ = aiter_events().__aiter__
            return ctx

        with patch.object(p._client.messages, "stream", side_effect=mock_stream_context):
            events = []
            async for event in p.stream(
                messages=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "list files"},
                ],
                tools=[
                    ToolSchema(
                        type="function",
                        function={
                            "name": "bash",
                            "description": "Run command",
                            "parameters": {
                                "type": "object",
                                "properties": {"command": {"type": "string"}},
                            },
                        },
                    )
                ],
            ):
                events.append(event)

        tool_events = [e for e in events if e.type == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call.name == "bash"
        assert tool_events[0].tool_call.arguments == {"command": "ls"}
        assert tool_events[0].tool_call.id == "toolu_1"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/providers/test_anthropic.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'coding_agent.providers.anthropic'`

- [ ] **Step 3: Write the Anthropic provider**

`coding-agent/src/coding_agent/providers/anthropic.py`:

```python
"""Anthropic native provider (Claude models)."""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic, APIError, RateLimitError, APIStatusError

from coding_agent.providers.base import (
    StreamEvent,
    ToolCall,
    ToolSchema,
)


class AnthropicProvider:
    """Anthropic provider using native API (not OpenAI-compatible).

    Translates between internal OpenAI-format messages and Anthropic's
    content block API format.
    """

    # All Claude models share 200k context
    DEFAULT_CONTEXT_SIZE = 200000

    def __init__(
        self,
        model: str,
        api_key: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ):
        if hasattr(api_key, "get_secret_value"):
            api_key = api_key.get_secret_value()
        self._model = model
        self._client = AsyncAnthropic(api_key=api_key)
        self._max_tokens = max_tokens
        self._temperature = temperature

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_size(self) -> int:
        return self.DEFAULT_CONTEXT_SIZE

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 1.0
    RETRY_STATUS_CODES = {429, 500, 502, 503, 529}

    def _convert_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert OpenAI-format messages to Anthropic format.

        Returns:
            (system_prompt, anthropic_messages)
        """
        system_parts: list[str] = []
        anthropic_msgs: list[dict[str, Any]] = []

        for msg in messages:
            role = msg["role"]

            if role == "system":
                system_parts.append(msg["content"])

            elif role == "user":
                anthropic_msgs.append({"role": "user", "content": msg["content"]})

            elif role == "assistant":
                # May have tool_calls → convert to content blocks
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    content_blocks = []
                    # Add text if present
                    if msg.get("content"):
                        content_blocks.append({"type": "text", "text": msg["content"]})
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        args_str = func.get("arguments", "{}")
                        try:
                            args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        except json.JSONDecodeError:
                            args = {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": func["name"],
                            "input": args,
                        })
                    anthropic_msgs.append({"role": "assistant", "content": content_blocks})
                else:
                    anthropic_msgs.append({"role": "assistant", "content": msg.get("content", "")})

            elif role == "tool":
                # Tool results → user message with tool_result content block
                anthropic_msgs.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg["tool_call_id"],
                            "content": msg.get("content", ""),
                        }
                    ],
                })

        return "\n\n".join(system_parts), anthropic_msgs

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Convert ToolSchema list to Anthropic tool format."""
        result = []
        for tool in tools:
            func = tool.function
            result.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream response from Anthropic API.

        Translates Anthropic streaming events to internal StreamEvent format.
        """
        system_prompt, anthropic_msgs = self._convert_messages(messages)

        api_kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": anthropic_msgs,
            "temperature": self._temperature,
        }
        if system_prompt:
            api_kwargs["system"] = system_prompt
        if tools:
            api_kwargs["tools"] = self._convert_tools(tools)

        try:
            # Track tool use blocks being accumulated
            tool_blocks: dict[int, dict[str, Any]] = {}

            for attempt in range(self.MAX_RETRIES):
                try:
                    async with self._client.messages.stream(**api_kwargs) as stream:
                        async for event in stream:
                            match event.type:
                                case "content_block_start":
                                    block = event.content_block
                                    if block.type == "tool_use":
                                        tool_blocks[event.index] = {
                                            "id": block.id,
                                            "name": block.name,
                                            "input_json": "",
                                        }
                                case "content_block_delta":
                                    delta = event.delta
                                    if delta.type == "text_delta":
                                        yield StreamEvent(type="delta", text=delta.text)
                                    elif delta.type == "input_json_delta":
                                        idx = event.index
                                        if idx in tool_blocks:
                                            tool_blocks[idx]["input_json"] += delta.partial_json
                                case "content_block_stop":
                                    idx = event.index
                                    if idx in tool_blocks:
                                        block = tool_blocks.pop(idx)
                                        try:
                                            args = json.loads(block["input_json"]) if block["input_json"] else {}
                                        except json.JSONDecodeError:
                                            args = {}
                                        yield StreamEvent(
                                            type="tool_call",
                                            tool_call=ToolCall(
                                                id=block["id"],
                                                name=block["name"],
                                                arguments=args,
                                            ),
                                        )
                                case "message_stop":
                                    pass  # handled below

                    yield StreamEvent(type="done")
                    return  # success, exit retry loop

                except RateLimitError:
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self.RETRY_DELAY_BASE * (2 ** attempt) + random.uniform(0, 1)
                        await asyncio.sleep(delay)
                        continue
                    raise
                except APIStatusError as e:
                    if e.status_code in self.RETRY_STATUS_CODES and attempt < self.MAX_RETRIES - 1:
                        delay = self.RETRY_DELAY_BASE * (2 ** attempt) + random.uniform(0, 1)
                        await asyncio.sleep(delay)
                        continue
                    raise

        except RateLimitError as e:
            yield StreamEvent(type="error", error=f"Rate limit exceeded: {e}")
        except APIStatusError as e:
            yield StreamEvent(type="error", error=f"API error {e.status_code}: {e}")
        except APIError as e:
            yield StreamEvent(type="error", error=f"API error: {e}")
        except Exception as e:
            yield StreamEvent(type="error", error=f"Unexpected error: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/providers/test_anthropic.py -v
```

Expected: PASS — all tests green

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/providers/anthropic.py coding-agent/tests/providers/test_anthropic.py
git commit -m "feat(p1): add Anthropic native provider with message format translation"
```

---

## Task 3: Plan Manager (core module)

**Files:**
- Create: `coding-agent/src/coding_agent/core/planner.py`
- Test: `coding-agent/tests/core/test_planner.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_planner.py`:

```python
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
        pm.set_tasks([
            {"title": "Read code", "status": "todo"},
            {"title": "Write tests", "status": "todo"},
        ])
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
        pm.set_tasks([
            {"title": "Read code", "status": "done"},
            {"title": "Write tests", "status": "in_progress"},
            {"title": "Implement", "status": "todo"},
            {"title": "Waiting on review", "status": "blocked"},
        ])
        text = pm.to_text()
        assert "[x] 1. Read code" in text
        assert "[>] 2. Write tests" in text
        assert "[ ] 3. Implement" in text
        assert "[!] 4. Waiting on review" in text

    def test_to_dict_roundtrip(self):
        pm = PlanManager()
        pm.set_tasks([
            {"title": "Read code", "status": "done"},
            {"title": "Write tests", "status": "todo"},
        ])
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
        pm.set_tasks([
            {"title": "Done task", "status": "done"},
            {"title": "Current task", "status": "in_progress"},
            {"title": "Next task", "status": "todo"},
        ])
        assert pm.next_task().title == "Current task"

    def test_next_task_skips_done(self):
        pm = PlanManager()
        pm.set_tasks([
            {"title": "Done", "status": "done"},
            {"title": "Also done", "status": "done"},
            {"title": "Todo", "status": "todo"},
        ])
        assert pm.next_task().title == "Todo"

    def test_next_task_none_when_all_done(self):
        pm = PlanManager()
        pm.set_tasks([{"title": "Done", "status": "done"}])
        assert pm.next_task() is None

    def test_next_task_none_when_empty(self):
        pm = PlanManager()
        assert pm.next_task() is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_planner.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`coding-agent/src/coding_agent/core/planner.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_planner.py -v
```

Expected: PASS — all tests green

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/planner.py coding-agent/tests/core/test_planner.py
git commit -m "feat(p1): add PlanManager with task CRUD and text rendering"
```

---

## Task 4: Planner Tools (todo_write, todo_read)

**Files:**
- Create: `coding-agent/src/coding_agent/tools/planner.py`
- Test: `coding-agent/tests/tools/test_planner_tool.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/tools/test_planner_tool.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_planner_tool.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`coding-agent/src/coding_agent/tools/planner.py`:

```python
"""Planner tools: todo_write and todo_read."""

from __future__ import annotations

import json
from typing import Any

from coding_agent.core.planner import PlanManager
from coding_agent.tools.registry import ToolRegistry


def register_planner_tools(registry: ToolRegistry, planner: PlanManager) -> None:
    """Register todo_write and todo_read tools."""

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
                planner.set_tasks(tasks)

            if updates is not None:
                for update in updates:
                    task_id = update.pop("id")
                    planner.update_task(task_id, **update)

            return json.dumps({
                "status": "ok",
                "plan": planner.to_text(),
                "task_count": len(planner.tasks),
            })
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    async def todo_read() -> str:
        """Read the current task plan."""
        return planner.to_text()

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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_planner_tool.py -v
```

Expected: PASS — all tests green

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/tools/planner.py coding-agent/tests/tools/test_planner_tool.py
git commit -m "feat(p1): add todo_write and todo_read tools"
```

---

## Task 5: Inject Plan into Context

**Files:**
- Modify: `coding-agent/src/coding_agent/core/context.py`
- Modify: `coding-agent/tests/core/test_context.py` (add plan injection tests)

- [ ] **Step 1: Write the failing tests**

Add to `coding-agent/tests/core/test_context.py`:

```python
# --- Add these tests to the existing test file ---

from coding_agent.core.planner import PlanManager


class TestPlanInjection:
    def test_no_plan_injected_when_none(self):
        ctx = Context(max_tokens=100000, system_prompt="You are an agent.")
        tape = Tape()
        tape.append(Entry.message("user", "hello"))
        msgs = ctx.build_working_set(tape)
        # Only system + user, no plan message
        assert len(msgs) == 2

    def test_plan_injected_after_system(self):
        planner = PlanManager()
        planner.set_tasks([
            {"title": "Read code", "status": "todo"},
            {"title": "Write tests", "status": "todo"},
        ])
        ctx = Context(max_tokens=100000, system_prompt="You are an agent.", planner=planner)
        tape = Tape()
        tape.append(Entry.message("user", "hello"))
        msgs = ctx.build_working_set(tape)
        # system + plan + user
        assert len(msgs) == 3
        assert msgs[1]["role"] == "system"
        assert "Current Plan" in msgs[1]["content"]
        assert "[ ] 1. Read code" in msgs[1]["content"]

    def test_empty_plan_not_injected(self):
        planner = PlanManager()
        ctx = Context(max_tokens=100000, system_prompt="You are an agent.", planner=planner)
        tape = Tape()
        tape.append(Entry.message("user", "hello"))
        msgs = ctx.build_working_set(tape)
        # Empty plan should not inject a message
        assert len(msgs) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context.py::TestPlanInjection -v
```

Expected: FAIL — `TypeError: Context.__init__() got an unexpected keyword argument 'planner'`

- [ ] **Step 3: Update Context to accept optional planner**

Modify `coding-agent/src/coding_agent/core/context.py`:

Change the `__init__` method:

```python
    def __init__(self, max_tokens: int, system_prompt: str, planner: PlanManager | None = None):
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.planner = planner
        self._max_chars = max_tokens * self.CHARS_PER_TOKEN
```

Add the import at the top (after existing imports):

```python
from coding_agent.core.planner import PlanManager
```

In `build_working_set`, after creating `system_msg` and before the anchor scan loop, inject the plan:

```python
    def build_working_set(self, tape: Tape) -> list[dict[str, Any]]:
        # System prompt always first
        system_msg = {"role": "system", "content": self.system_prompt}
        current_chars = len(self.system_prompt)

        # Inject plan if present and non-empty
        plan_msg = None
        if self.planner and self.planner.tasks:
            plan_text = f"[Current Plan]\n{self.planner.to_text()}"
            plan_msg = {"role": "system", "content": plan_text}
            current_chars += len(plan_text)

        # Find the last anchor to start from
        entries = tape.entries()
        start_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].kind == "anchor":
                start_idx = i
                break

        # Convert entries to messages, tracking token budget
        new_messages: list[dict[str, Any]] = []
        for entry in entries[start_idx:]:
            msg = self._entry_to_message(entry)
            if msg is not None:
                msg_text = self._message_to_text(msg)
                msg_chars = len(msg_text)

                if current_chars + msg_chars > self._max_chars:
                    if msg_chars > self._max_chars:
                        msg = self._truncate_message(msg, self._max_chars - current_chars)
                        if msg:
                            new_messages.append(msg)
                    else:
                        while new_messages and current_chars + msg_chars > self._max_chars:
                            removed = new_messages.pop(0)
                            current_chars -= len(self._message_to_text(removed))
                        new_messages.append(msg)
                        current_chars += msg_chars
                else:
                    new_messages.append(msg)
                    current_chars += msg_chars

        # Combine: system + plan (if present) + conversation messages
        result = [system_msg]
        if plan_msg:
            result.append(plan_msg)
        result.extend(new_messages)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context.py -v
```

Expected: PASS — all tests green (old + new)

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/context.py coding-agent/tests/core/test_context.py
git commit -m "feat(p1): inject plan into context working set"
```

---

## Task 6: Sub-Agent Module

**Files:**
- Create: `coding-agent/src/coding_agent/agents/__init__.py`
- Create: `coding-agent/src/coding_agent/agents/subagent.py`
- Create: `coding-agent/tests/agents/__init__.py`
- Test: `coding-agent/tests/agents/test_subagent.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/agents/__init__.py`:

```python
```

`coding-agent/tests/agents/test_subagent.py`:

```python
"""Tests for SubAgent dispatch."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from coding_agent.agents.subagent import SubAgent, SubAgentResult
from coding_agent.core.context import Context
from coding_agent.core.tape import Tape, Entry
from coding_agent.providers.base import StreamEvent, ToolCall, ToolSchema
from coding_agent.tools.registry import ToolRegistry
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)


class MockConsumer:
    def __init__(self):
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(call_id=req.call_id, decision="approve")


class MockProvider:
    """Provider that returns scripted responses."""

    def __init__(self, responses: list[list[StreamEvent]]):
        self._responses = responses
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "mock"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            yield StreamEvent(type="delta", text="No more responses")
            yield StreamEvent(type="done")


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_successful_subagent_run(self):
        """Sub-agent runs, succeeds, result is returned."""
        provider = MockProvider([
            [StreamEvent(type="delta", text="Done with sub-task"), StreamEvent(type="done")],
        ])
        tools = ToolRegistry()
        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        consumer = MockConsumer()

        subagent = SubAgent(
            provider=provider,
            consumer=consumer,
            max_steps=10,
        )
        result = await subagent.run(
            goal="Sub-task: read a file",
            parent_tape=parent_tape,
            tools=tools,
        )

        assert isinstance(result, SubAgentResult)
        assert result.success is True
        assert result.output == "Done with sub-task"

    @pytest.mark.asyncio
    async def test_subagent_forks_tape(self):
        """Sub-agent works on a forked tape, parent tape is unmodified during execution."""
        provider = MockProvider([
            [StreamEvent(type="delta", text="Sub result"), StreamEvent(type="done")],
        ])
        tools = ToolRegistry()
        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        original_count = len(parent_tape.entries())
        consumer = MockConsumer()

        subagent = SubAgent(provider=provider, consumer=consumer, max_steps=10)
        result = await subagent.run(
            goal="Sub-task",
            parent_tape=parent_tape,
            tools=tools,
        )

        # Sub-agent succeeded → parent tape should have merged entries
        assert result.success is True
        assert len(parent_tape.entries()) > original_count

    @pytest.mark.asyncio
    async def test_subagent_failure_does_not_merge(self):
        """Sub-agent that hits max_steps does not merge into parent tape."""
        # Provider always returns tool calls → will hit max_steps
        tool_call = ToolCall(id="c1", name="echo", arguments={"text": "loop"})
        responses = [
            [StreamEvent(type="tool_call", tool_call=tool_call), StreamEvent(type="done")]
            for _ in range(15)
        ]
        provider = MockProvider(responses)

        async def echo(text: str) -> str:
            return "echoed"

        tools = ToolRegistry()
        tools.register(
            name="echo",
            description="Echo text",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=echo,
        )

        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        original_entries = [e.to_dict() for e in parent_tape.entries()]
        consumer = MockConsumer()

        subagent = SubAgent(provider=provider, consumer=consumer, max_steps=3)
        result = await subagent.run(
            goal="This will fail",
            parent_tape=parent_tape,
            tools=tools,
        )

        assert result.success is False
        assert result.stop_reason == "max_steps_reached"
        # Parent tape should be unchanged
        current_entries = [e.to_dict() for e in parent_tape.entries()]
        assert len(current_entries) == len(original_entries)

    @pytest.mark.asyncio
    async def test_subagent_depth_limit(self):
        """Sub-agent respects max_depth."""
        provider = MockProvider([])
        tools = ToolRegistry()
        parent_tape = Tape()
        consumer = MockConsumer()

        subagent = SubAgent(
            provider=provider,
            consumer=consumer,
            max_steps=10,
            max_depth=2,
        )
        result = await subagent.run(
            goal="Too deep",
            parent_tape=parent_tape,
            tools=tools,
            depth=2,
        )

        assert result.success is False
        assert "depth" in result.output.lower()

    @pytest.mark.asyncio
    async def test_subagent_handoff_anchor_created(self):
        """Sub-agent creates a handoff anchor on the forked tape."""
        provider = MockProvider([
            [StreamEvent(type="delta", text="Done"), StreamEvent(type="done")],
        ])
        tools = ToolRegistry()
        parent_tape = Tape()
        parent_tape.append(Entry.message("user", "main task"))
        consumer = MockConsumer()

        subagent = SubAgent(provider=provider, consumer=consumer, max_steps=10)
        result = await subagent.run(
            goal="Sub-task goal",
            parent_tape=parent_tape,
            tools=tools,
        )

        assert result.success is True
        # After merge, parent tape should contain the subagent anchor
        anchors = [e for e in parent_tape.entries() if e.kind == "anchor"]
        assert len(anchors) >= 1
        assert anchors[0].payload["name"] == "subagent_start"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/agents/test_subagent.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`coding-agent/src/coding_agent/agents/__init__.py`:

```python
```

`coding-agent/src/coding_agent/agents/subagent.py`:

```python
"""SubAgent: fork tape, run independent loop, merge results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.core.tape import Tape

if TYPE_CHECKING:
    from coding_agent.providers.base import ChatProvider
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.wire import WireConsumer


@dataclass
class SubAgentResult:
    """Result of a sub-agent execution."""
    success: bool
    output: str
    stop_reason: str
    entries_count: int = 0


class SubAgent:
    """Dispatches a sub-agent on a forked tape.

    The sub-agent gets its own AgentLoop with a forked tape.
    On success, forked entries are merged back into the parent tape.
    On failure, the fork is discarded.
    """

    def __init__(
        self,
        provider: ChatProvider,
        consumer: WireConsumer,
        max_steps: int = 15,
        max_depth: int = 3,
        doom_threshold: int = 3,
    ):
        self.provider = provider
        self.consumer = consumer
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.doom_threshold = doom_threshold

    async def run(
        self,
        goal: str,
        parent_tape: Tape,
        tools: ToolRegistry,
        depth: int = 0,
    ) -> SubAgentResult:
        """Run a sub-agent on a forked tape.

        Args:
            goal: The sub-agent's task description
            parent_tape: Parent tape to fork from
            tools: Tool registry (can be restricted)
            depth: Current nesting depth

        Returns:
            SubAgentResult with success flag and output
        """
        if depth >= self.max_depth:
            return SubAgentResult(
                success=False,
                output=f"Max sub-agent depth ({self.max_depth}) reached",
                stop_reason="depth_limit",
            )

        # Fork tape for isolated execution
        forked_tape = parent_tape.fork()
        forked_tape.handoff("subagent_start", {"goal": goal, "depth": depth})

        # Create context for sub-agent
        context = Context(
            max_tokens=self.provider.max_context_size,
            system_prompt=(
                f"You are a sub-agent working on a specific task. "
                f"Your goal: {goal}\n"
                f"Focus only on this goal. Be concise."
            ),
        )

        # Run sub-agent loop
        loop = AgentLoop(
            provider=self.provider,
            tools=tools,
            tape=forked_tape,
            context=context,
            consumer=self.consumer,
            max_steps=self.max_steps,
            doom_threshold=self.doom_threshold,
        )

        outcome = await loop.run_turn(goal)

        success = outcome.stop_reason == "no_tool_calls"

        if success:
            # Merge forked entries back into parent tape
            parent_tape.merge(forked_tape)

        return SubAgentResult(
            success=success,
            output=outcome.final_message or "",
            stop_reason=outcome.stop_reason,
            entries_count=len(forked_tape.entries()),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/agents/test_subagent.py -v
```

Expected: PASS — all tests green

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/agents/ coding-agent/tests/agents/
git commit -m "feat(p1): add SubAgent with fork/merge tape execution"
```

---

## Task 7: Sub-Agent Tool

**Files:**
- Create: `coding-agent/src/coding_agent/tools/subagent.py`
- Test: `coding-agent/tests/tools/test_subagent_tool.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/tools/test_subagent_tool.py`:

```python
"""Tests for subagent tool registration."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from coding_agent.agents.subagent import SubAgent
from coding_agent.core.tape import Tape
from coding_agent.providers.base import StreamEvent, ToolSchema
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.subagent import register_subagent_tool
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)


class MockConsumer:
    def __init__(self):
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(call_id=req.call_id, decision="approve")


class MockProvider:
    def __init__(self, responses: list[list[StreamEvent]]):
        self._responses = responses
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "mock"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            yield StreamEvent(type="delta", text="fallback")
            yield StreamEvent(type="done")


class TestSubagentTool:
    @pytest.mark.asyncio
    async def test_tool_registered(self):
        provider = MockProvider([])
        tape = Tape()
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )
        assert "subagent" in registry.list_tools()

    @pytest.mark.asyncio
    async def test_tool_dispatches_subagent(self):
        provider = MockProvider([
            [StreamEvent(type="delta", text="Sub-task done"), StreamEvent(type="done")],
        ])
        tape = Tape()
        tape.append(Tape._create_entry_for_test("user", "main goal"))  # won't exist, use Entry
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        result = await registry.execute("subagent", {"goal": "Read the README"})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert "Sub-task done" in parsed["output"]

    @pytest.mark.asyncio
    async def test_tool_returns_json_result(self):
        provider = MockProvider([
            [StreamEvent(type="delta", text="Result here"), StreamEvent(type="done")],
        ])
        tape = Tape()
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        result = await registry.execute("subagent", {"goal": "Do something"})
        parsed = json.loads(result)
        assert "success" in parsed
        assert "output" in parsed
        assert "stop_reason" in parsed
```

Wait — I used a nonexistent `_create_entry_for_test`. Let me fix the test:

`coding-agent/tests/tools/test_subagent_tool.py`:

```python
"""Tests for subagent tool registration."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from coding_agent.core.tape import Entry, Tape
from coding_agent.providers.base import StreamEvent, ToolSchema
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.subagent import register_subagent_tool
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)


class MockConsumer:
    def __init__(self):
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(call_id=req.call_id, decision="approve")


class MockProvider:
    def __init__(self, responses: list[list[StreamEvent]]):
        self._responses = responses
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "mock"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            yield StreamEvent(type="delta", text="fallback")
            yield StreamEvent(type="done")


class TestSubagentTool:
    @pytest.mark.asyncio
    async def test_tool_registered(self):
        provider = MockProvider([])
        tape = Tape()
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )
        assert "subagent" in registry.list_tools()

    @pytest.mark.asyncio
    async def test_tool_dispatches_subagent(self):
        provider = MockProvider([
            [StreamEvent(type="delta", text="Sub-task done"), StreamEvent(type="done")],
        ])
        tape = Tape()
        tape.append(Entry.message("user", "main goal"))
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        result = await registry.execute("subagent", {"goal": "Read the README"})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert "Sub-task done" in parsed["output"]

    @pytest.mark.asyncio
    async def test_tool_returns_json_result(self):
        provider = MockProvider([
            [StreamEvent(type="delta", text="Result here"), StreamEvent(type="done")],
        ])
        tape = Tape()
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        result = await registry.execute("subagent", {"goal": "Do something"})
        parsed = json.loads(result)
        assert "success" in parsed
        assert "output" in parsed
        assert "stop_reason" in parsed
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_subagent_tool.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`coding-agent/src/coding_agent/tools/subagent.py`:

```python
"""Sub-agent dispatch tool."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from coding_agent.agents.subagent import SubAgent
from coding_agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from coding_agent.core.tape import Tape
    from coding_agent.providers.base import ChatProvider
    from coding_agent.wire import WireConsumer


def register_subagent_tool(
    registry: ToolRegistry,
    provider: ChatProvider,
    tape: Tape,
    consumer: WireConsumer,
    max_steps: int = 15,
    max_depth: int = 3,
) -> None:
    """Register the subagent dispatch tool."""

    subagent = SubAgent(
        provider=provider,
        consumer=consumer,
        max_steps=max_steps,
        max_depth=max_depth,
    )

    async def dispatch_subagent(goal: str) -> str:
        """Dispatch a sub-agent to work on a specific sub-task.

        Args:
            goal: Clear description of what the sub-agent should accomplish
        """
        result = await subagent.run(
            goal=goal,
            parent_tape=tape,
            tools=registry,
        )
        return json.dumps({
            "success": result.success,
            "output": result.output,
            "stop_reason": result.stop_reason,
            "entries_count": result.entries_count,
        })

    registry.register(
        name="subagent",
        description=(
            "Dispatch a sub-agent to work on a specific sub-task independently. "
            "The sub-agent gets its own context and tool access. Use this for: "
            "reading large codebases, running tests in isolation, or any task "
            "that can be done independently. The sub-agent's results are merged "
            "back if successful."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Clear, specific description of the sub-task",
                },
            },
            "required": ["goal"],
        },
        handler=dispatch_subagent,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_subagent_tool.py -v
```

Expected: PASS — all tests green

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/tools/subagent.py coding-agent/tests/tools/test_subagent_tool.py
git commit -m "feat(p1): add subagent dispatch tool"
```

---

## Task 8: CLI Integration

**Files:**
- Modify: `coding-agent/src/coding_agent/__main__.py`

- [ ] **Step 1: Update CLI to support provider selection and register P1 tools**

`coding-agent/src/coding_agent/__main__.py`:

```python
"""CLI entry point: python -m coding_agent"""

import click


@click.group()
def main():
    """Coding Agent CLI."""
    pass


@main.command()
@click.option("--goal", required=True, help="Task goal for the agent")
@click.option("--repo", default=".", help="Repository path")
@click.option("--model", default="gpt-4o", help="Model name")
@click.option("--provider", "provider_name", default="openai", type=click.Choice(["openai", "anthropic"]), help="LLM provider")
@click.option("--base-url", default=None, help="OpenAI-compatible API base URL")
@click.option("--api-key", envvar="AGENT_API_KEY", required=True, help="API key")
@click.option("--max-steps", default=30, help="Max steps per turn")
@click.option("--approval", default="yolo", type=click.Choice(["yolo", "interactive", "auto"]))
def run(goal, repo, model, provider_name, base_url, api_key, max_steps, approval):
    """Run agent on a goal (batch mode)."""
    import asyncio
    from coding_agent.core.config import Config

    config = Config(
        provider=provider_name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        repo=repo,
        max_steps=max_steps,
        approval_mode=approval,
    )
    asyncio.run(_run(config, goal))


async def _run(config, goal):
    from coding_agent.core.loop import AgentLoop
    from coding_agent.core.planner import PlanManager
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.tools.file import register_file_tools
    from coding_agent.tools.shell import register_shell_tools
    from coding_agent.tools.search import register_search_tools
    from coding_agent.tools.planner import register_planner_tools
    from coding_agent.tools.subagent import register_subagent_tool
    from coding_agent.core.tape import Tape
    from coding_agent.core.context import Context
    from coding_agent.ui.headless import HeadlessConsumer

    tape = Tape.create(config.tape_dir)
    provider = _create_provider(config)

    planner = PlanManager()
    registry = ToolRegistry()
    register_file_tools(registry, repo_root=config.repo)
    register_shell_tools(registry, cwd=config.repo)
    register_search_tools(registry, repo_root=config.repo)
    register_planner_tools(registry, planner)

    consumer = HeadlessConsumer()

    # Register subagent tool (needs provider, tape, consumer)
    register_subagent_tool(
        registry=registry,
        provider=provider,
        tape=tape,
        consumer=consumer,
        max_steps=config.subagent_max_steps,
        max_depth=config.max_subagent_depth,
    )

    system_prompt = (
        "You are a coding agent. You can read files, edit files, "
        "run shell commands, search the codebase, create task plans, "
        "and dispatch sub-agents for independent sub-tasks.\n\n"
        "Always create a plan (todo_write) before starting complex work. "
        "Update task status as you progress."
    )
    context = Context(provider.max_context_size, system_prompt, planner=planner)

    loop = AgentLoop(
        provider=provider,
        tools=registry,
        tape=tape,
        context=context,
        consumer=consumer,
        max_steps=config.max_steps,
    )

    result = await loop.run_turn(goal)
    click.echo(f"\n--- Result ({result.stop_reason}) ---")
    if result.final_message:
        click.echo(result.final_message)


def _create_provider(config):
    """Create the appropriate provider based on config."""
    if config.provider == "anthropic":
        from coding_agent.providers.anthropic import AnthropicProvider
        return AnthropicProvider(
            model=config.model,
            api_key=config.api_key,
        )
    else:
        from coding_agent.providers.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI still works**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run python -m coding_agent run --help
```

Expected: Shows help with `--provider` option listed

- [ ] **Step 3: Run all tests to verify nothing broken**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest -v
```

Expected: ALL tests pass

- [ ] **Step 4: Commit**

```bash
git add coding-agent/src/coding_agent/__main__.py
git commit -m "feat(p1): wire planner, subagent, and provider selection into CLI"
```

---

## Task 9: E2E Integration Test

**Files:**
- Create: `coding-agent/tests/test_e2e_p1.py`

- [ ] **Step 1: Write E2E test with mock provider**

`coding-agent/tests/test_e2e_p1.py`:

```python
"""E2E integration test for P1: planner + subagent with mock provider."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.core.planner import PlanManager
from coding_agent.core.tape import Entry, Tape
from coding_agent.providers.base import StreamEvent, ToolCall, ToolSchema
from coding_agent.tools.planner import register_planner_tools
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.subagent import register_subagent_tool
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)


class MockConsumer:
    def __init__(self):
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(call_id=req.call_id, decision="approve")


class MockProvider:
    """Provider with scripted responses for E2E test."""

    def __init__(self, responses: list[list[StreamEvent]]):
        self._responses = responses
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "mock"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            yield StreamEvent(type="delta", text="No more responses")
            yield StreamEvent(type="done")


class TestE2EP1:
    @pytest.mark.asyncio
    async def test_agent_creates_plan_then_completes(self):
        """Agent uses todo_write to create a plan, then responds."""
        # Step 1: LLM calls todo_write to create a plan
        # Step 2: LLM responds with final text
        provider = MockProvider([
            # Step 1: Call todo_write
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="call_1",
                        name="todo_write",
                        arguments={
                            "tasks": [
                                {"title": "Read the code", "status": "in_progress"},
                                {"title": "Fix the bug", "status": "todo"},
                            ]
                        },
                    ),
                ),
                StreamEvent(type="done"),
            ],
            # Step 2: Final response
            [
                StreamEvent(type="delta", text="Plan created. Starting work."),
                StreamEvent(type="done"),
            ],
        ])

        planner = PlanManager()
        registry = ToolRegistry()
        register_planner_tools(registry, planner)

        tape = Tape()
        consumer = MockConsumer()
        context = Context(128000, "You are a coding agent.", planner=planner)

        loop = AgentLoop(
            provider=provider,
            tools=registry,
            tape=tape,
            context=context,
            consumer=consumer,
            max_steps=10,
        )

        result = await loop.run_turn("Fix the bug in main.py")

        assert result.stop_reason == "no_tool_calls"
        assert "Plan created" in result.final_message

        # Verify plan was created
        assert len(planner.tasks) == 2
        assert planner.tasks[0].title == "Read the code"

        # Verify plan is in context
        messages = context.build_working_set(tape)
        plan_msgs = [m for m in messages if m.get("role") == "system" and "Current Plan" in m.get("content", "")]
        assert len(plan_msgs) == 1

    @pytest.mark.asyncio
    async def test_agent_dispatches_subagent(self):
        """Agent dispatches a sub-agent via the subagent tool."""
        # Main agent calls subagent tool
        # Sub-agent provider returns a simple response
        call_index = [0]

        class SequencedProvider:
            """Provider that serves both main agent and sub-agent."""

            def __init__(self):
                self._call_index = 0

            @property
            def model_name(self) -> str:
                return "mock"

            @property
            def max_context_size(self) -> int:
                return 128000

            async def stream(
                self,
                messages: list[dict[str, Any]],
                tools: list[ToolSchema] | None = None,
                **kwargs: Any,
            ) -> AsyncIterator[StreamEvent]:
                self._call_index += 1
                if self._call_index == 1:
                    # Main agent: dispatch subagent
                    yield StreamEvent(
                        type="tool_call",
                        tool_call=ToolCall(
                            id="call_1",
                            name="subagent",
                            arguments={"goal": "Read the README file"},
                        ),
                    )
                    yield StreamEvent(type="done")
                elif self._call_index == 2:
                    # Sub-agent: respond
                    yield StreamEvent(type="delta", text="README contains project docs")
                    yield StreamEvent(type="done")
                else:
                    # Main agent: final response
                    yield StreamEvent(type="delta", text="Sub-agent found the README info.")
                    yield StreamEvent(type="done")

        provider = SequencedProvider()
        tape = Tape()
        consumer = MockConsumer()
        registry = ToolRegistry()

        register_subagent_tool(
            registry=registry,
            provider=provider,
            tape=tape,
            consumer=consumer,
        )

        context = Context(128000, "You are a coding agent.")
        loop = AgentLoop(
            provider=provider,
            tools=registry,
            tape=tape,
            context=context,
            consumer=consumer,
            max_steps=10,
        )

        result = await loop.run_turn("What's in the README?")

        assert result.stop_reason == "no_tool_calls"
        assert "Sub-agent" in result.final_message or "README" in result.final_message

        # Verify tape has subagent entries (merged from fork)
        entries = tape.entries()
        kinds = [e.kind for e in entries]
        assert "anchor" in kinds  # subagent_start anchor
        assert "tool_call" in kinds
        assert "tool_result" in kinds
```

- [ ] **Step 2: Run E2E test**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/test_e2e_p1.py -v
```

Expected: PASS — both E2E tests green

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest -v
```

Expected: ALL tests pass (P0 + P1)

- [ ] **Step 4: Commit**

```bash
git add coding-agent/tests/test_e2e_p1.py
git commit -m "test(p1): add E2E integration tests for planner and subagent"
```

---

## Summary

| Task | Component | Tests | LOC |
|------|-----------|-------|-----|
| 1 | Anthropic dependency | — | 5 |
| 2 | Anthropic Provider | 8 | 180 |
| 3 | PlanManager | 14 | 80 |
| 4 | Planner tools | 7 | 70 |
| 5 | Context plan injection | 3 | 20 |
| 6 | SubAgent module | 5 | 80 |
| 7 | SubAgent tool | 3 | 50 |
| 8 | CLI integration | — | 40 |
| 9 | E2E tests | 2 | 120 |
| **Total** | | **~42** | **~645** |

P1 exit criteria: Agent creates a plan before acting. Can dispatch sub-agents for independent tasks. Supports both OpenAI and Anthropic providers.
