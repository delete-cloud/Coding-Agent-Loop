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


def create_tool_panel(
    name: str,
    args: dict[str, Any],
    result: str | None = None,
    timing_text: Text | None = None,
) -> Panel:
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
    
    # Build title with optional timing
    title_parts = [f"[bold]{icon} {name}[/]", status]
    if timing_text is not None:
        title_parts.append(str(timing_text))
    title = " ".join(title_parts)
    
    return Panel(
        Group(*content_parts),
        title=title,
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
