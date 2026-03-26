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
        # Prevent unbounded growth - keep last 20 tools
        if len(self.tools) > 20:
            self.tools = self.tools[-20:]
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
        # Prevent unbounded growth - keep last 50 messages
        if len(self.messages) > 50:
            self.messages = self.messages[-50:]
        self.refresh()

    def __enter__(self) -> CodingAgentTUI:
        """Enter TUI context - initialize Live display."""
        self.live = Live(
            self.layout,
            refresh_per_second=10,
            screen=True,
            console=self.console,
        )
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit TUI context - cleanup Live display."""
        if self.live:
            self.live.__exit__(exc_type, exc_val, exc_tb)
            self.live = None
