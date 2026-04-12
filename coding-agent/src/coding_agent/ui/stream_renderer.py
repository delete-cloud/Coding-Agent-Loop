from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Final

from rich.cells import cell_len
from rich.console import Console
from rich.control import Control
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.segment import ControlType
from rich.text import Text

from coding_agent.ui.components import create_message_panel
from coding_agent.ui.theme import theme

_SPINNER: Final[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

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


_DIFF_GIT_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
_DIFF_UNIFIED_HEADER = re.compile(r"^--- a/.+\n\+\+\+ b/", re.MULTILINE)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _sanitize_display_text(text: str) -> str:
    return _strip_ansi(text).replace("\r", "")


def _is_diff_output(text: str) -> bool:
    if not text:
        return False
    return bool(_DIFF_GIT_HEADER.search(text) or _DIFF_UNIFIED_HEADER.search(text))


def _smart_truncate(
    text: str, max_lines: int = 50, tail_lines: int = 5
) -> tuple[str, bool]:
    if not text:
        return text, False
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text, False
    head_lines = max_lines - tail_lines
    hidden = len(lines) - max_lines
    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    indicator = f"… [{hidden} lines hidden] …"
    return "\n".join([*head, indicator, *tail]), True


def _render_diff(text: str) -> Text:
    result = Text()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i > 0:
            result.append("\n")
        if line.startswith("diff --git "):
            result.append(line, style=theme.Colors.DIFF_META)
        elif line.startswith("index "):
            result.append(line, style=theme.Colors.DIFF_META)
        elif line.startswith("+++ "):
            result.append(line, style=theme.Colors.DIFF_META)
        elif line.startswith("--- "):
            result.append(line, style=theme.Colors.DIFF_META)
        elif line.startswith("@@ "):
            result.append(line, style=theme.Colors.DIFF_HUNK)
        elif line.startswith("+"):
            result.append(line, style=theme.Colors.DIFF_ADD)
        elif line.startswith("-"):
            result.append(line, style=theme.Colors.DIFF_DELETE)
        else:
            result.append(line)
    return result


def _compact_call_summary(name: str, args: dict[str, object]) -> str:
    if name == "bash_run":
        command = str(args.get("command", "")).strip()
        return command[:80] + "…" if len(command) > 80 else command

    if name in {"file_write", "file_replace", "file_patch"}:
        path = str(args.get("path", args.get("file_path", ""))).strip()
        return path

    if name == "subagent":
        goal = str(args.get("goal", "")).strip()
        return goal[:60] + "…" if len(goal) > 60 else goal

    return ""


def _extract_path_from_result(result: str) -> str:
    try:
        data: object = json.loads(result)
    except Exception:
        return ""
    if isinstance(data, Mapping):
        path = data.get("path")
        if path is not None:
            return str(path)
    return ""


def _compact_result_summary(name: str, result: str, is_error: bool) -> str:
    lines = [line for line in result.strip().split("\n") if line]

    if is_error:
        return lines[0][:80] if lines else ""

    if name == "bash_run":
        if not lines:
            return ""
        if len(lines) == 1:
            return lines[0][:80]
        return f"{len(lines)} lines output"

    if name in {"file_write", "file_replace", "file_patch"}:
        path = _extract_path_from_result(result)
        if path:
            return path
        return lines[0][:80] if lines else ""

    if name == "subagent":
        return lines[0][:80] if lines else ""

    return lines[0][:80] if lines else ""


class StreamingRenderer:
    def __init__(
        self, console: Console | None = None, *, enhanced_boundaries: bool = False
    ) -> None:
        self.console: Console = console or Console(force_terminal=True, soft_wrap=False)
        self._enhanced_boundaries: bool = enhanced_boundaries
        self._stream_buffer: str = ""
        self._in_stream: bool = False
        self._tool_start_times: dict[str, float] = {}
        self._stream_started_output: bool = False
        self._spinner_idx: int = 0
        self._thinking_active: bool = False
        self._thinking_buffer: str = ""

    def user_message(self, content: str) -> None:
        clean_content = _sanitize_display_text(content)
        if self._enhanced_boundaries and self.console.is_terminal:
            self.console.print(
                create_message_panel("user", clean_content, console=self.console)
            )
        else:
            self.console.print(Text("❯ ", style="bold green"), end="")
            self.console.print(Text(clean_content, style="bold white"))

    def thinking(self, text: str) -> None:
        if self._thinking_active:
            self._thinking_buffer += text
            return
        self.console.print(Text(text, style="dim italic"))

    def _clear_current_line(self) -> None:
        if self.console.is_terminal:
            self.console.control(
                Control(
                    ControlType.CARRIAGE_RETURN,
                    (ControlType.ERASE_IN_LINE, 2),
                )
            )

    def thinking_start(self) -> None:
        self._thinking_active = True
        self._spinner_idx = 0
        self._thinking_buffer = ""
        if self.console.is_terminal:
            char = _SPINNER[self._spinner_idx]
            self.console.print(Text(f"{char} Thinking...", style="dim"), end="")

    def thinking_update(self, elapsed_seconds: float = 0.0) -> None:
        if not self._thinking_active:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER)
        if self.console.is_terminal:
            self._clear_current_line()
            char = _SPINNER[self._spinner_idx]
            elapsed_str = f" {int(elapsed_seconds)}s" if elapsed_seconds >= 1 else ""
            self.console.print(
                Text(f"{char} Thinking...{elapsed_str}", style="dim"), end=""
            )

    def thinking_end(self) -> None:
        if not self._thinking_active:
            return
        if self.console.is_terminal:
            self._clear_current_line()
        if self._thinking_buffer:
            self.console.print(Text(self._thinking_buffer, style="dim italic"))
        self._thinking_active = False
        self._spinner_idx = 0
        self._thinking_buffer = ""

    def update_status(
        self,
        tokens_in: int = 0,
        tokens_out: int = 0,
        elapsed_seconds: float = 0.0,
        model: str = "",
        context_pct: float = 0.0,
    ) -> None:
        if not self.console.is_terminal:
            return
        if self._in_stream:
            return
        parts: list[str] = []
        if model:
            parts.append(model)
        if context_pct > 0:
            parts.append(f"{context_pct:.0f}%")
        parts.append(f"{tokens_in} in / {tokens_out} out")
        if elapsed_seconds > 0:
            parts.append(f"{elapsed_seconds:.1f}s")
        line = " | ".join(parts)
        self._clear_current_line()
        self.console.print(Text(line, style="dim"), end="")

    def stream_start(self) -> None:
        self._stream_buffer = ""
        self._in_stream = True
        self._stream_started_output = False

    def stream_text(self, text: str) -> None:
        if not text:
            return

        self._stream_buffer += text
        self.console.print(text, end="", soft_wrap=True, highlight=False, markup=False)
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
                    md = Markdown(self._stream_buffer)
                    if self._enhanced_boundaries:
                        self.console.print(
                            create_message_panel("assistant", md, console=self.console)
                        )
                    else:
                        self.console.print(md)
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
        if self._in_stream:
            self.stream_end()

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

        clean_result = _strip_ansi(result)
        display_result, _ = _smart_truncate(clean_result)

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

        if _is_diff_output(display_result):
            renderable = _render_diff(display_result)
        else:
            renderable = display_result

        panel = Panel(
            renderable,
            title=f"{status} {icon} [bold]{name}[/]{timing}",
            border_style=style,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def compact_tool_call(
        self, call_id: str, name: str, args: dict[str, object]
    ) -> None:
        self._flush_stream()
        self._tool_start_times[call_id] = time.perf_counter()

        icon = _tool_icon(name)
        summary = _compact_call_summary(name, args)

        line = Text()
        line.append(f"⏳ {icon} ", style="yellow")
        line.append(name, style="bold")
        if summary:
            line.append("  ", style="dim")
            line.append(summary, style="dim")
        self.console.print(line)

    def compact_tool_result(
        self, call_id: str, name: str, result: str, *, is_error: bool = False
    ) -> None:
        duration = 0.0
        if call_id in self._tool_start_times:
            duration = time.perf_counter() - self._tool_start_times.pop(call_id)

        icon = _tool_icon(name)
        status = "✗" if is_error else "✓"
        style = "red" if is_error else "green"
        summary = _compact_result_summary(name, _strip_ansi(result), is_error)

        line = Text()
        line.append(f"{status} {icon} ", style=style)
        line.append(name, style="bold")
        if summary:
            line.append("  ", style="dim")
            line.append(summary, style="dim")

        if duration >= 5.0:
            line.append(f" ({duration:.1f}s)", style="red bold")
        elif duration >= 1.0:
            line.append(f" ({duration:.1f}s)", style="yellow")
        elif duration > 0:
            line.append(f" ({duration:.2f}s)", style="dim")

        self.console.print(line)

        if is_error and name == "bash_run":
            clean_result = _strip_ansi(result)
            excerpt, _ = _smart_truncate(clean_result, max_lines=5, tail_lines=2)
            if excerpt:
                self.console.print(Text(excerpt, style="red dim"))

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
        if self._enhanced_boundaries and self.console.is_terminal:
            self.console.print(
                Rule(
                    characters=theme.Layout.SEPARATOR_CHAR, style=theme.Colors.SEPARATOR
                )
            )
