# Coding Agent TUI (Rich + Layout) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a beautiful terminal UI for the coding agent using rich library with Layout, featuring real-time streaming output, tool call visualization, and plan progress tracking.

**Architecture:** A `CodingAgentTUI` class wraps the existing `AgentLoop`, intercepting events via a custom `WireConsumer` to update rich components. Uses `Live` display with `Layout` for responsive panels.

**Tech Stack:** Python 3.12+, rich (Console, Live, Layout, Panel, Table, Syntax), asyncio

---

## File Map

```
coding-agent/
  src/coding_agent/
    ui/
      rich_tui.py          # Main TUI class with Layout
      theme.py             # Centralized colors/icons/theme
      components.py        # Reusable UI components (MessagePanel, ToolPanel)
    wire.py                # Existing wire protocol (add RichConsumer)
  tests/
    ui/
      test_rich_tui.py     # TUI component tests
```

---

## Task 1: Create Theme Module

**Files:**
- Create: `coding-agent/src/coding_agent/ui/theme.py`

- [ ] **Step 1: Create centralized theme configuration**

`coding-agent/src/coding_agent/ui/theme.py`:

```python
"""Centralized theme configuration for Coding Agent TUI."""

from rich.box import Box
from rich.style import Style


class Theme:
    """TUI theme with colors, icons, and layout constants."""

    # ==================== COLORS ====================
    class Colors:
        PRIMARY = "cyan"
        SUCCESS = "bold green"
        ERROR = "bold red"
        WARNING = "bold yellow"
        INFO = "blue"
        TEXT_PRIMARY = "white"
        TEXT_MUTED = "dim white"
        BORDER_DEFAULT = "cyan"
        BORDER_ACTIVE = "bold cyan"
        USER_MSG = "green"
        ASSISTANT_MSG = "blue"
        SYSTEM_MSG = "yellow"

    # ==================== ICONS ====================
    class Icons:
        AGENT = "🤖"
        USER = "👤"
        TOOL = "🔧"
        PLAN = "📋"
        SUCCESS = "✅"
        ERROR = "❌"
        WARNING = "⚠️"
        THINKING = "💭"
        FILE = "📄"
        SEARCH = "🔍"
        BASH = "⚡"

    # ==================== LAYOUT ====================
    class Layout:
        HEADER_HEIGHT = 3
        TOOL_PANEL_HEIGHT = 12
        PANEL_PADDING = (0, 1)
        BOX_STYLE = "ROUNDED"

    # ==================== TEXT STYLES ====================
    class Styles:
        TITLE = "bold cyan"
        HEADER = "bold white"
        SUBTITLE = "dim cyan"
        CODE = "dim"


# Global theme instance
theme = Theme()
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/ui/theme.py
git commit -m "feat(tui): add centralized theme module"
```

---

## Task 2: Create UI Components

**Files:**
- Create: `coding-agent/src/coding_agent/ui/components.py`

- [ ] **Step 1: Create reusable UI components**

`coding-agent/src/coding_agent/ui/components.py`:

```python
"""Reusable UI components for Coding Agent TUI."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from coding_agent.ui.theme import theme


def create_message_panel(role: str, content: str, is_streaming: bool = False) -> Panel:
    """Create a message panel for user/assistant messages."""
    if role == "user":
        icon = theme.Icons.USER
        color = theme.Colors.USER_MSG
        title = "You"
    else:
        icon = theme.Icons.AGENT
        color = theme.Colors.ASSISTANT_MSG
        title = "Agent"
    
    if is_streaming:
        title += " " + theme.Icons.THINKING
    
    text = Text(content, style=color)
    
    return Panel(
        text,
        title=f"[bold]{icon} {title}[/]",
        border_style=color,
        padding=theme.Layout.PANEL_PADDING,
    )


def create_tool_panel(name: str, args: dict[str, Any], result: str | None = None) -> Panel:
    """Create a tool call visualization panel."""
    # Build args display
    args_text = Text()
    for key, value in args.items():
        args_text.append(f"{key}=", style="dim")
        args_text.append(f"{value!r}\n", style="cyan")
    
    content_parts = [args_text]
    
    # Add result if available
    if result is not None:
        result_text = Text("\n" + "─" * 40 + "\n", style="dim")
        result_text.append("Result:\n", style="bold")
        # Truncate long results
        if len(result) > 500:
            result = result[:500] + "..."
        result_text.append(result, style="green")
        content_parts.append(result_text)
    
    # Choose icon based on tool name
    icon = theme.Icons.TOOL
    if "file" in name.lower():
        icon = theme.Icons.FILE
    elif "grep" in name.lower() or "search" in name.lower():
        icon = theme.Icons.SEARCH
    elif "bash" in name.lower():
        icon = theme.Icons.BASH
    
    status = theme.Icons.THINKING if result is None else theme.Icons.SUCCESS
    
    return Panel(
        Group(*content_parts),
        title=f"[bold]{icon} {name}[/] {status}",
        border_style=theme.Colors.INFO if result is None else theme.Colors.SUCCESS,
        padding=theme.Layout.PANEL_PADDING,
    )


def create_plan_panel(tasks: list[dict[str, Any]]) -> Panel:
    """Create a plan progress panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Status", width=3)
    table.add_column("Task", style="white")
    
    status_icons = {
        "todo": "[ ]",
        "in_progress": "[bold yellow][>][/]",
        "done": "[bold green][x][/]",
        "blocked": "[bold red][!][/]",
    }
    
    for task in tasks:
        status = task.get("status", "todo")
        title = task.get("title", "Unknown")
        icon = status_icons.get(status, "[ ]")
        table.add_row(icon, title)
    
    return Panel(
        table,
        title=f"[bold]{theme.Icons.PLAN} Plan[/]",
        border_style=theme.Colors.PRIMARY,
        padding=theme.Layout.PANEL_PADDING,
    )


def create_header_panel(model: str, step: int, max_steps: int) -> Panel:
    """Create the header panel with status info."""
    text = Text()
    text.append(f"{theme.Icons.AGENT} ", style="bold")
    text.append("Coding Agent", style=theme.Styles.TITLE)
    text.append("  |  ", style="dim")
    text.append(f"Model: ", style="dim")
    text.append(model, style="cyan")
    text.append("  |  ", style="dim")
    text.append(f"Step: ", style="dim")
    text.append(f"{step}/{max_steps}", style="yellow" if step < max_steps else "green")
    
    return Panel(
        text,
        border_style=theme.Colors.BORDER_DEFAULT,
        padding=(0, 1),
    )
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/ui/components.py
git commit -m "feat(tui): add reusable UI components"
```

---

## Task 3: Create RichConsumer Wire Consumer

**Files:**
- Create: `coding-agent/src/coding_agent/ui/rich_consumer.py`
- Modify: `coding-agent/src/coding_agent/wire.py` (add import)

- [ ] **Step 1: Create RichConsumer that integrates with TUI**

`coding-agent/src/coding_agent/ui/rich_consumer.py`:

```python
"""Rich TUI consumer that renders wire messages to rich components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coding_agent.ui.theme import theme
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    StepInfo,
    StreamDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnBegin,
    TurnEnd,
    WireConsumer,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.rich_tui import CodingAgentTUI


class RichConsumer(WireConsumer):
    """WireConsumer that renders to Rich TUI."""

    def __init__(self, tui: CodingAgentTUI) -> None:
        self.tui = tui
        self.current_tool: dict[str, Any] | None = None

    async def emit(self, msg: WireMessage) -> None:
        """Emit a message to the TUI."""
        match msg:
            case TurnBegin():
                self.tui.start_turn()
            
            case TurnEnd(stop_reason=reason, final_message=text):
                self.tui.end_turn(reason, text)
            
            case StreamDelta(text=text):
                if text:
                    self.tui.append_stream(text)
            
            case ToolCallBegin(call_id=cid, tool=tool, args=args):
                self.current_tool = {
                    "id": cid,
                    "name": tool,
                    "args": args,
                    "result": None,
                }
                self.tui.show_tool_call(tool, args)
            
            case ToolCallEnd(call_id=cid, result=result):
                if self.current_tool and self.current_tool["id"] == cid:
                    self.current_tool["result"] = result
                    self.tui.update_tool_result(result)
                self.current_tool = None
            
            case StepInfo(current=current, total=total):
                self.tui.update_step(current, total)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Request approval from user via TUI."""
        # For now, auto-approve in TUI mode (yolo)
        # TODO: Add interactive approval prompt
        return ApprovalResponse(
            call_id=req.call_id,
            decision="approve",
            scope="once",
        )
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/ui/rich_consumer.py
git commit -m "feat(tui): add RichConsumer wire consumer"
```

---

## Task 4: Create Main TUI Class

**Files:**
- Create: `coding-agent/src/coding_agent/ui/rich_tui.py`

- [ ] **Step 1: Create main CodingAgentTUI class**

`coding-agent/src/coding_agent/ui/rich_tui.py`:

```python
"""Main Rich TUI for Coding Agent."""

from __future__ import annotations

from typing import Any

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from coding_agent.ui.components import (
    create_header_panel,
    create_message_panel,
    create_plan_panel,
    create_tool_panel,
)
from coding_agent.ui.rich_consumer import RichConsumer
from coding_agent.ui.theme import theme


class CodingAgentTUI:
    """Rich-based Terminal UI for Coding Agent."""

    def __init__(self, model_name: str = "gpt-4", max_steps: int = 30) -> None:
        self.console = Console()
        self.model_name = model_name
        self.max_steps = max_steps
        self.current_step = 0
        
        # Content state
        self.messages: list[dict[str, Any]] = []
        self.current_stream = ""
        self.tools: list[dict[str, Any]] = []
        self.plan_tasks: list[dict[str, Any]] = []
        
        # Layout
        self.layout = self._create_layout()
        self.live: Live | None = None
        
        # Create consumer
        self.consumer = RichConsumer(self)

    def _create_layout(self) -> Layout:
        """Create the main layout structure."""
        layout = Layout()
        
        # Split into header, main, and tools
        layout.split_column(
            Layout(name="header", size=theme.Layout.HEADER_HEIGHT),
            Layout(name="main"),
            Layout(name="tools", size=theme.Layout.TOOL_PANEL_HEIGHT),
        )
        
        # Main area: conversation (left) + status (right)
        layout["main"].split_row(
            Layout(name="conversation", ratio=3),
            Layout(name="status", ratio=1),
        )
        
        return layout

    def _render_header(self) -> Panel:
        """Render the header panel."""
        return create_header_panel(self.model_name, self.current_step, self.max_steps)

    def _render_conversation(self) -> Panel:
        """Render the conversation panel."""
        panels = []
        
        # Render previous messages
        for msg in self.messages:
            panels.append(
                create_message_panel(msg["role"], msg["content"])
            )
        
        # Render current streaming message if any
        if self.current_stream:
            panels.append(
                create_message_panel("assistant", self.current_stream, is_streaming=True)
            )
        
        if not panels:
            panels = [Text("Waiting for input...", style="dim")]
        
        return Panel(
            Group(*panels),
            title="[bold]Conversation[/]",
            border_style=theme.Colors.PRIMARY,
        )

    def _render_status(self) -> Panel:
        """Render the status panel with plan."""
        if self.plan_tasks:
            return create_plan_panel(self.plan_tasks)
        else:
            return Panel(
                Text("No active plan", style="dim"),
                title="[bold]Plan[/]",
                border_style=theme.Colors.TEXT_MUTED,
            )

    def _render_tools(self) -> Panel:
        """Render the tools panel."""
        if not self.tools:
            return Panel(
                Text("No tool calls yet", style="dim"),
                title="[bold]Tools[/]",
                border_style=theme.Colors.TEXT_MUTED,
            )
        
        # Show last 2 tool calls
        panels = []
        for tool in self.tools[-2:]:
            panels.append(
                create_tool_panel(
                    tool["name"],
                    tool["args"],
                    tool.get("result"),
                )
            )
        
        return Panel(
            Group(*panels),
            title=f"[bold]Recent Tools ({len(self.tools)} total)[/]",
            border_style=theme.Colors.INFO,
        )

    def refresh(self) -> None:
        """Refresh the TUI display."""
        if self.live:
            self.layout["header"].update(self._render_header())
            self.layout["conversation"].update(self._render_conversation())
            self.layout["status"].update(self._render_status())
            self.layout["tools"].update(self._render_tools())

    # Event handlers called by RichConsumer
    def start_turn(self) -> None:
        """Called when a new turn begins."""
        self.current_stream = ""
        self.refresh()

    def end_turn(self, reason: str, message: str | None) -> None:
        """Called when a turn ends."""
        if message:
            self.messages.append({
                "role": "assistant",
                "content": message,
            })
        self.current_stream = ""
        self.refresh()

    def append_stream(self, text: str) -> None:
        """Append streaming text."""
        self.current_stream += text
        self.refresh()

    def show_tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Show a tool call."""
        self.tools.append({
            "name": name,
            "args": args,
            "result": None,
        })
        self.refresh()

    def update_tool_result(self, result: str) -> None:
        """Update the result of the last tool call."""
        if self.tools:
            self.tools[-1]["result"] = result
            self.refresh()

    def update_step(self, current: int, total: int) -> None:
        """Update step counter."""
        self.current_step = current
        self.max_steps = total
        self.refresh()

    def update_plan(self, tasks: list[dict[str, Any]]) -> None:
        """Update plan tasks."""
        self.plan_tasks = tasks
        self.refresh()

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        self.messages.append({
            "role": "user",
            "content": content,
        })
        self.refresh()

    def run(self) -> None:
        """Run the TUI (context manager)."""
        self.live = Live(
            self.layout,
            refresh_per_second=10,
            screen=True,
            console=self.console,
        )
        return self.live

    def __enter__(self) -> CodingAgentTUI:
        self.run().__enter__()
        return self

    def __exit__(self, *args) -> None:
        if self.live:
            self.live.__exit__(*args)
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/ui/rich_tui.py
git commit -m "feat(tui): add main CodingAgentTUI class with Layout"
```

---

## Task 5: Update CLI to Support TUI Mode

**Files:**
- Modify: `coding-agent/src/coding_agent/__main__.py`

- [ ] **Step 1: Add --tui flag and TUI integration**

Add to `coding-agent/src/coding_agent/__main__.py`:

```python
# Add new option
@click.option("--tui", is_flag=True, help="Use Rich TUI interface")
```

And in `_run()`:

```python
async def _run(config, goal, use_tui: bool = False):
    # ... existing imports ...
    from coding_agent.ui.rich_tui import CodingAgentTUI
    
    tape = Tape.create(config.tape_dir)
    provider = _create_provider(config)
    
    planner = PlanManager()
    registry = ToolRegistry()
    register_file_tools(registry, repo_root=config.repo)
    register_shell_tools(registry, cwd=config.repo)
    register_search_tools(registry, repo_root=config.repo)
    register_planner_tools(registry, planner)
    
    if use_tui:
        # TUI mode
        tui = CodingAgentTUI(model_name=config.model, max_steps=config.max_steps)
        consumer = tui.consumer
        
        # Register subagent tool
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
        
        with tui:
            tui.add_user_message(goal)
            result = await loop.run_turn(goal)
            click.echo(f"\n--- Result ({result.stop_reason}) ---")
    else:
        # Headless mode (existing code)
        # ... keep existing headless implementation ...
```

- [ ] **Step 2: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/__main__.py
git commit -m "feat(tui): add --tui flag to CLI"
```

---

## Task 6: Write Tests

**Files:**
- Create: `coding-agent/tests/ui/test_rich_tui.py`

- [ ] **Step 1: Write TUI component tests**

`coding-agent/tests/ui/test_rich_tui.py`:

```python
"""Tests for Rich TUI components."""

import pytest
from rich.panel import Panel

from coding_agent.ui.components import (
    create_header_panel,
    create_message_panel,
    create_plan_panel,
    create_tool_panel,
)


class TestComponents:
    def test_create_message_panel_user(self):
        panel = create_message_panel("user", "Hello")
        assert isinstance(panel, Panel)
        assert "You" in panel.title
    
    def test_create_message_panel_assistant(self):
        panel = create_message_panel("assistant", "Hi there")
        assert isinstance(panel, Panel)
        assert "Agent" in panel.title
    
    def test_create_tool_panel(self):
        panel = create_tool_panel("bash", {"command": "ls"}, "output")
        assert isinstance(panel, Panel)
        assert "bash" in panel.title
        assert "✅" in panel.title  # Success icon
    
    def test_create_tool_panel_no_result(self):
        panel = create_tool_panel("file_read", {"path": "test.py"})
        assert isinstance(panel, Panel)
        assert "💭" in panel.title  # Thinking icon
    
    def test_create_plan_panel(self):
        tasks = [
            {"title": "Task 1", "status": "done"},
            {"title": "Task 2", "status": "in_progress"},
        ]
        panel = create_plan_panel(tasks)
        assert isinstance(panel, Panel)
    
    def test_create_header_panel(self):
        panel = create_header_panel("gpt-4", 5, 10)
        assert isinstance(panel, Panel)
        assert "Coding Agent" in str(panel.renderable)


class TestCodingAgentTUI:
    def test_tui_initialization(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI(model_name="test-model", max_steps=20)
        assert tui.model_name == "test-model"
        assert tui.max_steps == 20
        assert tui.current_step == 0
    
    def test_add_user_message(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.add_user_message("Test message")
        assert len(tui.messages) == 1
        assert tui.messages[0]["role"] == "user"
        assert tui.messages[0]["content"] == "Test message"
    
    def test_append_stream(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.append_stream("Hello")
        assert tui.current_stream == "Hello"
        tui.append_stream(" World")
        assert tui.current_stream == "Hello World"
    
    def test_show_tool_call(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.show_tool_call("bash", {"command": "ls"})
        assert len(tui.tools) == 1
        assert tui.tools[0]["name"] == "bash"
    
    def test_update_tool_result(self):
        from coding_agent.ui.rich_tui import CodingAgentTUI
        
        tui = CodingAgentTUI()
        tui.show_tool_call("bash", {"command": "ls"})
        tui.update_tool_result("file1.py file2.py")
        assert tui.tools[0]["result"] == "file1.py file2.py"
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/ui/test_rich_tui.py -v
```

Expected: PASS - all tests green

- [ ] **Step 3: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add tests/ui/test_rich_tui.py
git commit -m "test(tui): add TUI component tests"
```

---

## Task 7: Final Integration and Test

**Files:**
- Modify: `coding-agent/src/coding_agent/ui/__init__.py` (create if not exists)

- [ ] **Step 1: Create UI package init**

`coding-agent/src/coding_agent/ui/__init__.py`:

```python
"""UI package for Coding Agent."""

from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.ui.rich_consumer import RichConsumer
from coding_agent.ui.rich_tui import CodingAgentTUI

__all__ = [
    "HeadlessConsumer",
    "RichConsumer",
    "CodingAgentTUI",
]
```

- [ ] **Step 2: Run full test suite**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/ -v
```

Expected: PASS - all tests green

- [ ] **Step 3: Test TUI manually**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run python -m coding_agent run \
  --goal "List Python files in src directory" \
  --api-key $RDC_API_KEY \
  --tui
```

- [ ] **Step 4: Commit**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
git add src/coding_agent/ui/__init__.py
git commit -m "feat(tui): complete TUI integration"
```

---

## Summary

| Task | Component | Files | LOC Est. |
|------|-----------|-------|----------|
| 1 | Theme module | theme.py | 50 |
| 2 | UI Components | components.py | 100 |
| 3 | RichConsumer | rich_consumer.py | 60 |
| 4 | Main TUI | rich_tui.py | 200 |
| 5 | CLI Integration | __main__.py | 30 |
| 6 | Tests | test_rich_tui.py | 100 |
| **Total** | | | **~540** |

Exit Criteria: Agent can run with `--tui` flag showing real-time streaming, tool calls, and plan progress in a beautiful Rich interface.
