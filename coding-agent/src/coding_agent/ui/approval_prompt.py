"""Interactive approval prompt with tool-specific previews.

Shows users what a tool is about to do and lets them approve, reject,
or approve for the entire session.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse


class ApprovalChoice(str, Enum):
    APPROVE_ONCE = "approve_once"
    APPROVE_SESSION = "approve_session"
    REJECT = "reject"
    REJECT_WITH_REASON = "reject_with_reason"


def format_tool_preview(console: Console, req: ApprovalRequest) -> None:
    """Render a tool-specific preview panel.

    bash → command syntax panel
    file_write → file path + content preview
    file_edit → old_text/new_text diff
    other → generic key=value display
    """
    tool = req.tool
    args = req.args

    if tool == "bash" or "bash" in tool.lower():
        _preview_bash(console, args)
    elif tool == "file_write" or "write" in tool.lower():
        _preview_file_write(console, args)
    elif tool == "file_edit" or "edit" in tool.lower():
        _preview_file_edit(console, args)
    else:
        _preview_generic(console, tool, args)


def _preview_bash(console: Console, args: dict[str, Any]) -> None:
    command = args.get("command", "")
    panel = Panel(
        Syntax(command, "bash", theme="monokai", line_numbers=False),
        title="\u26a1 [bold yellow]bash[/] \u2014 Command to execute",
        border_style="yellow",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


def _preview_file_write(console: Console, args: dict[str, Any]) -> None:
    path = args.get("path", "unknown")
    content = args.get("content", "")

    if len(content) > 500:
        display = content[:500] + f"\n\u2026 ({len(content) - 500} more chars)"
    else:
        display = content

    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    lang_map = {"py": "python", "js": "javascript", "ts": "typescript", "sh": "bash"}
    lang = lang_map.get(ext, ext)

    panel = Panel(
        Syntax(display, lang or "text", theme="monokai", line_numbers=True),
        title=f"\U0001f4c4 [bold cyan]file_write[/] \u2192 {path}",
        border_style="cyan",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


def _preview_file_edit(console: Console, args: dict[str, Any]) -> None:
    path = args.get("path", "unknown")
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")

    diff_text = Text()
    diff_text.append(f"File: {path}\n\n", style="bold")
    for line in old_text.splitlines():
        diff_text.append(f"- {line}\n", style="red")
    for line in new_text.splitlines():
        diff_text.append(f"+ {line}\n", style="green")

    panel = Panel(
        diff_text,
        title="\U0001f4dd [bold]file_edit[/] \u2014 Changes",
        border_style="yellow",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


def _preview_generic(console: Console, tool: str, args: dict[str, Any]) -> None:
    args_parts = []
    for key, value in args.items():
        val_str = str(value)
        if len(val_str) > 200:
            val_str = val_str[:200] + "\u2026"
        args_parts.append(f"[dim]{key}=[/] {val_str}")
    args_text = "\n".join(args_parts) if args_parts else "[dim]no arguments[/]"

    panel = Panel(
        args_text,
        title=f"\U0001f527 [bold]{tool}[/]",
        border_style="yellow",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


async def prompt_approval(console: Console, req: ApprovalRequest) -> ApprovalResponse:
    """Show approval prompt and collect user decision.

    Displays tool-specific preview then prompts:
    [y]=approve  [a]=approve all (session)  [n]=reject  [r]=reject with reason
    """
    console.print()
    console.print("[yellow bold]\u26a0 Approval Required[/]")
    format_tool_preview(console, req)
    console.print()
    console.print(
        "[bold][green]y[/]=approve  "
        "[cyan]a[/]=approve all (session)  "
        "[red]n[/]=reject  "
        "[yellow]r[/]=reject with reason[/]"
    )

    choice = await asyncio.get_event_loop().run_in_executor(
        None, lambda: input("\u2192 ").strip().lower()
    )

    if choice in ("y", "yes", ""):
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=True,
            scope="once",
        )
    elif choice in ("a", "all"):
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=True,
            scope="session",
        )
    elif choice in ("r", "reason"):
        reason = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("Reason: ").strip()
        )
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=False,
            feedback=reason or "Rejected by user",
        )
    else:
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=False,
            feedback="Rejected by user",
        )
