from __future__ import annotations

import re
import time
from typing import Final

from rich.cells import cell_len
from rich.console import Console
from rich.control import Control
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import ControlType
from rich.text import Text

_MAX_RESULT_DISPLAY = 1000

_TOOL_ICONS: Final[dict[str, str]] = {
    "file": "📄",
    "grep": "🔍",
    "search": "🔍",
    "bash": "⚡",
    "glob": "📂",
    "todo": "📋",
}

_MD_PATTERN = re.compile(
    "|".join(
        (
            r"```",
            r"^#{1,6}\s",
            r"\*\*[^*]+\*\*",
            r"__[^_]+__",
            r"\[.+?\]\(.+?\)",
            r"^[*\-]\s",
            r"^\d+\.\s",
            r"^>\s",
            r"^\|.+\|",
        )
    ),
    re.MULTILINE,
)


def _tool_icon(name: str) -> str:
    for pattern, icon in _TOOL_ICONS.items():
        if pattern in name.lower():
            return icon
    return "🔧"


def _has_markdown_syntax(text: str) -> bool:
    return bool(_MD_PATTERN.search(text))


class StreamingRenderer:
    def __init__(self, console: Console | None = None) -> None:
        self.console: Console = console or Console()
        self._stream_buffer: str = ""
        self._in_stream: bool = False
        self._tool_start_times: dict[str, float] = {}
        self._stream_started_output: bool = False

    def user_message(self, content: str) -> None:
        self.console.print(Text("❯ ", style="bold green"), end="")
        self.console.print(Text(content, style="bold white"))

    def thinking(self, text: str) -> None:
        self.console.print(Text(text, style="dim italic"))

    def stream_start(self) -> None:
        """Start a new streaming text block."""
        self._stream_buffer = ""
        self._in_stream = True
        self._stream_started_output = False

    def stream_text(self, text: str) -> None:
        """Append streaming text directly to the terminal."""
        if not text:
            return

        self._stream_buffer += text
        self.console.print(text, end="", soft_wrap=True, highlight=False)
        self._stream_started_output = True

    def _count_terminal_lines(self, text: str) -> int:
        if not text:
            return 0

        width = self.console.size.width
        if width <= 0:
            width = 80

        total = 0
        for segment in text.split("\n"):
            segment_width = cell_len(segment)
            if segment_width == 0:
                total += 1
            else:
                total += (segment_width + width - 1) // width
        return total

    def _clear_streamed_output(self, line_count: int) -> None:
        if line_count <= 0 or not self.console.is_terminal:
            return

        controls: list[ControlType | tuple[ControlType, int]] = [
            ControlType.CARRIAGE_RETURN,
            (ControlType.ERASE_IN_LINE, 2),
        ]
        for _ in range(line_count - 1):
            controls.append((ControlType.CURSOR_UP, 1))
            controls.append((ControlType.ERASE_IN_LINE, 2))

        self.console.control(Control(*controls))

    def stream_end(self) -> None:
        if self._stream_started_output and self._stream_buffer:
            should_rerender = self.console.is_terminal and _has_markdown_syntax(
                self._stream_buffer
            )

            if should_rerender:
                try:
                    line_count = self._count_terminal_lines(self._stream_buffer)
                    self._clear_streamed_output(line_count)
                    self.console.print(Markdown(self._stream_buffer))
                except Exception:
                    self.console.print()
            else:
                self.console.print()
        elif self._stream_started_output:
            self.console.print()

        self._stream_buffer = ""
        self._in_stream = False
        self._stream_started_output = False

    def _flush_stream(self) -> None:
        """Flush any active stream (called before tool calls interrupt text)."""
        if self._in_stream:
            self.stream_end()

    # ── Tool calls ──

    def tool_call(self, call_id: str, name: str, args: dict[str, object]) -> None:
        self._flush_stream()
        self._tool_start_times[call_id] = time.perf_counter()

        icon = _tool_icon(name)

        args_parts: list[str] = []
        for key, value in args.items():
            val_str = str(value)
            if len(val_str) > 100:
                val_str = val_str[:100] + "…"
            args_parts.append(f"[dim]{key}=[/]{val_str}")
        args_text = "\n".join(args_parts) if args_parts else "[dim]no arguments[/]"

        panel = Panel(
            args_text,
            title=f"{icon} [bold]{name}[/]",
            border_style="dim cyan",
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def tool_result(
        self, call_id: str, name: str, result: str, *, is_error: bool = False
    ) -> None:
        duration = 0.0
        if call_id in self._tool_start_times:
            duration = time.perf_counter() - self._tool_start_times.pop(call_id)

        truncated = False
        display_result = result
        if len(display_result) > _MAX_RESULT_DISPLAY:
            display_result = display_result[:_MAX_RESULT_DISPLAY]
            truncated = True

        icon = _tool_icon(name)

        if is_error:
            style = "red"
            status = "✗"
        else:
            style = "green"
            status = "✓"

        if duration >= 5.0:
            timing = f" [red bold]({duration:.1f}s) ⚠[/]"
        elif duration >= 1.0:
            timing = f" [yellow]({duration:.1f}s)[/]"
        elif duration > 0:
            timing = f" [dim]({duration:.2f}s)[/]"
        else:
            timing = ""

        result_text = display_result
        if truncated:
            result_text += (
                f"\n[dim]… ({len(result) - _MAX_RESULT_DISPLAY} chars truncated)[/]"
            )

        panel = Panel(
            result_text,
            title=f"{status} {icon} [bold]{name}[/]{timing}",
            border_style=style,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def collapsed_group(
        self,
        summary: str,
        duration: float,
        has_error: bool = False,
        hint: str | None = None,
    ) -> None:
        self._flush_stream()
        indicator = (
            Text("⚠ ", style="yellow") if has_error else Text("✓ ", style="green")
        )
        line = Text()
        line.append_text(indicator)
        line.append(summary, style="dim")
        if hint:
            line.append("  ", style="dim")
            line.append(hint, style="dim italic")
        if duration >= 5.0:
            line.append(f" ({duration:.1f}s)", style="red bold")
        elif duration >= 1.0:
            line.append(f" ({duration:.1f}s)", style="yellow")
        elif duration > 0:
            line.append(f" ({duration:.2f}s)", style="dim")
        self.console.print(line)

    def turn_end(self, status: str) -> None:
        if self._in_stream:
            self.stream_end()
        if status == "error":
            self.console.print(Text("⚠ Turn ended with an error", style="red"))
