"""Main Rich TUI for Coding Agent."""

from __future__ import annotations

import time
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


class ToolExecutionTracker:
    """Track tool execution times."""

    def __init__(self):
        self._start_times: dict[str, float] = {}
        self._durations: dict[str, float] = {}

    def start(self, call_id: str) -> None:
        """Start tracking a tool call."""
        self._start_times[call_id] = time.perf_counter()

    def end(self, call_id: str) -> float:
        """End tracking and return duration."""
        if call_id not in self._start_times:
            return 0.0

        duration = time.perf_counter() - self._start_times[call_id]
        self._durations[call_id] = duration
        return duration

    def format_duration(self, duration: float) -> Text:
        """Format duration with color coding.

        Color scheme:
        - < 1s: default dim color
        - 1-5s: yellow warning
        - > 5s: red bold with warning mark
        """
        if duration < 1.0:
            # < 1s: default dim color
            return Text(f"({duration:.2f}s)", style="dim")
        elif duration < 5.0:
            # 1-5s: yellow warning
            return Text(f"({duration:.2f}s)", style="yellow")
        else:
            # > 5s: red bold with warning
            return Text(f"({duration:.2f}s) ⚠", style="red bold")


class _TuiRendererAdapter:
    def __init__(self, tui: "CodingAgentTUI") -> None:
        self._tui = tui
        self.console = tui.console

    def thinking_start(self) -> None:
        self._tui.thinking_start()

    def thinking_update(self, elapsed_seconds: float = 0.0) -> None:
        self._tui.thinking_update(elapsed_seconds=elapsed_seconds)

    def thinking(self, text: str) -> None:
        self._tui.stream_text(text)

    def thinking_end(self) -> None:
        self._tui.thinking_end()

    def stream_start(self) -> None:
        self._tui.stream_start()

    def stream_text(self, text: str) -> None:
        self._tui.stream_text(text)

    def stream_end(self) -> None:
        self._tui.stream_end()

    def tool_call(self, call_id: str, name: str, args: dict[str, object]) -> None:
        self._tui.tool_call(call_id, name, args)

    def compact_tool_call(
        self, call_id: str, name: str, args: dict[str, object]
    ) -> None:
        self._tui.compact_tool_call(call_id, name, args)

    def tool_result(
        self, call_id: str, name: str, result: str, *, is_error: bool = False
    ) -> None:
        self._tui.tool_result(call_id, name, result, is_error=is_error)

    def compact_tool_result(
        self, call_id: str, name: str, result: str, *, is_error: bool = False
    ) -> None:
        self._tui.compact_tool_result(call_id, name, result, is_error=is_error)

    def collapsed_group(
        self,
        summary: str,
        duration: float,
        has_error: bool = False,
        hint: str | None = None,
    ) -> None:
        self._tui.collapsed_group(summary, duration, has_error=has_error, hint=hint)

    def turn_end(self, status: str) -> None:
        self._tui.turn_end(status)


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

        # Tool execution tracking
        self._tool_tracker = ToolExecutionTracker()

        # Create consumer
        self.consumer = RichConsumer(_TuiRendererAdapter(self))

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
            panels.append(create_message_panel(msg["role"], msg["content"]))

        # Render current streaming message if any
        if self.current_stream:
            panels.append(
                create_message_panel(
                    "assistant", self.current_stream, is_streaming=True
                )
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

        # Show last 2 tool calls with timing
        panels = []
        for tool in self.tools[-2:]:
            # Format timing if available
            timing_text = None
            if tool.get("duration") is not None:
                timing_text = self._tool_tracker.format_duration(tool["duration"])

            panels.append(
                create_tool_panel(
                    tool["name"],
                    tool["args"],
                    tool.get("result"),
                    timing_text=timing_text,
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

    def thinking_start(self) -> None:
        self.refresh()

    def thinking_update(self, elapsed_seconds: float = 0.0) -> None:
        del elapsed_seconds
        self.refresh()

    def thinking_end(self) -> None:
        self.refresh()

    def stream_start(self) -> None:
        self.current_stream = ""
        self.refresh()

    def stream_text(self, text: str) -> None:
        self.current_stream += text
        self.refresh()

    def stream_end(self) -> None:
        self.refresh()

    def tool_call(self, call_id: str, name: str, args: dict[str, object]) -> None:
        self.show_tool_call(call_id, name, dict(args))

    def compact_tool_call(
        self, call_id: str, name: str, args: dict[str, object]
    ) -> None:
        self.tool_call(call_id, name, args)

    def tool_result(
        self, call_id: str, name: str, result: str, *, is_error: bool = False
    ) -> None:
        del name, is_error
        self.update_tool_result(call_id, result)

    def compact_tool_result(
        self, call_id: str, name: str, result: str, *, is_error: bool = False
    ) -> None:
        self.tool_result(call_id, name, result, is_error=is_error)

    def collapsed_group(
        self,
        summary: str,
        duration: float,
        has_error: bool = False,
        hint: str | None = None,
    ) -> None:
        suffix = f" ({duration:.2f}s)" if duration > 0 else ""
        detail = f" — {hint}" if hint else ""
        prefix = "⚠ " if has_error else "✓ "
        self.tools.append(
            {
                "call_id": f"collapsed-{len(self.tools)}",
                "name": f"{prefix}{summary}",
                "args": {},
                "result": f"{summary}{detail}{suffix}",
                "duration": duration if duration > 0 else None,
            }
        )
        if len(self.tools) > 20:
            self.tools = self.tools[-20:]
        self.refresh()

    def turn_end(self, status: str) -> None:
        if status == "error":
            self.messages.append(
                {"role": "assistant", "content": "Turn ended with an error"}
            )
        self.current_stream = ""
        self.refresh()

    # Event handlers called by RichConsumer
    def start_turn(self) -> None:
        """Called when a new turn begins."""
        self.current_stream = ""
        self.refresh()

    def end_turn(self, reason: str, message: str | None) -> None:
        """Called when a turn ends."""
        if message:
            self.messages.append(
                {
                    "role": "assistant",
                    "content": message,
                }
            )
        self.current_stream = ""
        self.refresh()

    def append_stream(self, text: str) -> None:
        """Append streaming text."""
        self.stream_text(text)

    def show_tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        """Show a tool call."""
        # Start tracking this tool call
        self._tool_tracker.start(call_id)

        self.tools.append(
            {
                "call_id": call_id,
                "name": name,
                "args": args,
                "result": None,
                "duration": None,
            }
        )
        # Prevent unbounded growth - keep last 20 tools
        if len(self.tools) > 20:
            self.tools = self.tools[-20:]
        self.refresh()

    def update_tool_result(self, call_id: str, result: str) -> None:
        """Update the result of a tool call."""
        # End tracking and get duration
        duration = self._tool_tracker.end(call_id)

        # Find the tool call by call_id and update it
        for tool in reversed(self.tools):
            if tool.get("call_id") == call_id:
                tool["result"] = result
                tool["duration"] = duration
                break
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
        self.messages.append(
            {
                "role": "user",
                "content": content,
            }
        )
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
