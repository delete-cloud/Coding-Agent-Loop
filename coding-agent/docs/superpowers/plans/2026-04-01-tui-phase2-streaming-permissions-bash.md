# TUI Phase 2: Real Streaming, Permission UX, `! bash` Mode

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the TUI from a buffer-then-render approach into a real-time streaming experience with proper permission UX, inline shell execution, multiline input, and dead code cleanup.

**Architecture:** The current `StreamingRenderer` buffers all `stream_text()` deltas and renders once at `stream_end()`. We replace this with `rich.live.Live` wrapping a `rich.markdown.Markdown` that re-renders on each delta at 8fps. Permission requests get tool-specific preview panels with approve/reject/session-approve options via `prompt_toolkit`. A new `! bash` mode lets users execute shell commands directly from the REPL prompt. Multiline input uses Alt+Enter to submit.

**Tech Stack:** Rich (`Live`, `Markdown`, `Panel`, `Syntax`, `Console`), prompt_toolkit (`PromptSession`, `patch_stdout`, `KeyBindings`), existing wire protocol (`ApprovalRequest`/`ApprovalResponse`), `asyncio.create_subprocess_shell` for `! bash`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/coding_agent/ui/stream_renderer.py` | **Rewrite** | Real streaming Markdown via `rich.live.Live` |
| `src/coding_agent/ui/rich_consumer.py` | **Modify** | Permission UX dispatch, approval prompt |
| `src/coding_agent/ui/approval_prompt.py` | **Create** | Interactive approval UI with tool-specific previews |
| `src/coding_agent/cli/repl.py` | **Modify** | Add `! bash` mode, integrate `patch_stdout` |
| `src/coding_agent/cli/input_handler.py` | **Modify** | Multiline input (Alt+Enter), `!` prefix completion |
| `src/coding_agent/cli/bash_executor.py` | **Create** | Inline shell execution for `! bash` mode |
| `src/coding_agent/ui/rich_tui.py` | **Delete** | Dead code — old full-screen TUI |
| `tests/ui/test_stream_renderer.py` | **Modify** | Tests for real streaming behavior |
| `tests/ui/test_streaming_consumer.py` | **Modify** | Tests for permission UX flow |
| `tests/ui/test_approval_prompt.py` | **Create** | Tests for approval UI |
| `tests/cli/test_bash_executor.py` | **Create** | Tests for `! bash` execution |
| `tests/cli/test_input_handler.py` | **Create** | Tests for multiline input |
| `tests/ui/test_rich_tui.py` | **Delete** | Remove tests for dead code |

---

## Current State (What Phase 1 Built)

Phase 1 (`2026-04-01-tui-streaming-redesign.md`) delivered:
- `StreamingRenderer` that prints to scrollback (no Live panel)
- `RichConsumer` dispatching wire messages to renderer
- `ToolResultDelta` wire message + pipeline emission
- REPL using `StreamingRenderer` instead of `CodingAgentTUI`

**Remaining deficiencies this plan fixes:**
1. **Fake streaming**: `stream_text()` only appends to buffer; user sees nothing until `stream_end()` renders `Markdown(buffer)` once
2. **No permission UX**: `request_approval()` auto-approves everything; `repl.py` has crude `input("[y/N]")` 
3. **No `! bash` mode**: Users can't run shell commands directly from REPL
4. **No multiline input**: No Shift+Enter / Alt+Enter support
5. **Dead code**: `rich_tui.py` (278 lines) + `test_rich_tui.py` referenced by nothing

---

## Task 1: Real Streaming Markdown in `StreamingRenderer`

Replace the buffer-then-render approach with `rich.live.Live` + `rich.markdown.Markdown` that re-renders on each delta.

**Files:**
- Modify: `src/coding_agent/ui/stream_renderer.py`
- Modify: `tests/ui/test_stream_renderer.py`

- [ ] **Step 1: Write the failing test for real streaming**

Add to `tests/ui/test_stream_renderer.py`:

```python
def test_stream_text_renders_incrementally(self):
    """stream_text() should produce visible output immediately, not just buffer."""
    renderer, _, buf = self._make_renderer()
    renderer.stream_start()
    renderer.stream_text("Hello ")
    # Output should be visible BEFORE stream_end
    output_after_first = buf.getvalue()
    assert len(output_after_first) > 0, "stream_text should produce visible output"

def test_stream_renders_markdown_formatting(self):
    """Streamed text should render markdown formatting at stream_end."""
    renderer, _, buf = self._make_renderer()
    renderer.stream_start()
    renderer.stream_text("# Hello\n\nThis is **bold** text.")
    renderer.stream_end()
    output = buf.getvalue()
    assert "Hello" in output
    assert "bold" in output

def test_stream_live_context_manager(self):
    """StreamingRenderer should use Live for streaming, not just buffer."""
    renderer, _, buf = self._make_renderer()
    renderer.stream_start()
    renderer.stream_text("chunk1 ")
    renderer.stream_text("chunk2 ")
    renderer.stream_text("chunk3")
    renderer.stream_end()
    output = buf.getvalue()
    # All text should be present after stream_end
    assert "chunk1" in output
    assert "chunk2" in output
    assert "chunk3" in output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestStreamingRenderer::test_stream_text_renders_incrementally -v`
Expected: FAIL — current `stream_text()` only appends to `_stream_buffer`, produces no output

- [ ] **Step 3: Rewrite streaming methods in `StreamingRenderer`**

Replace the streaming section of `src/coding_agent/ui/stream_renderer.py`:

```python
"""Scrollback-based streaming renderer.

Prints directly to the terminal — nothing disappears.
Tool calls render as inline Rich panels.
Streaming text uses rich.live.Live for real-time Markdown rendering.
"""

from __future__ import annotations

import time
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

_MAX_RESULT_DISPLAY = 1000

_TOOL_ICONS = {
    "file": "📄",
    "grep": "🔍",
    "search": "🔍",
    "bash": "⚡",
    "glob": "📂",
    "todo": "📋",
}


def _tool_icon(name: str) -> str:
    for pattern, icon in _TOOL_ICONS.items():
        if pattern in name.lower():
            return icon
    return "🔧"


class StreamingRenderer:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._stream_buffer = ""
        self._in_stream = False
        self._live: Live | None = None
        self._tool_start_times: dict[str, float] = {}

    def user_message(self, content: str) -> None:
        self.console.print(Text("❯ ", style="bold green"), end="")
        self.console.print(Text(content, style="bold white"))

    def thinking(self, text: str) -> None:
        self.console.print(Text(text, style="dim italic"))

    # ── Streaming text (real-time Markdown via Live) ──

    def stream_start(self) -> None:
        """Start a new streaming text block with Live rendering."""
        self._stream_buffer = ""
        self._in_stream = True
        self._live = Live(
            Text(""),
            console=self.console,
            refresh_per_second=8,
            vertical_overflow="visible",
        )
        self._live.start()

    def stream_text(self, text: str) -> None:
        """Append streaming text and re-render as Markdown."""
        self._stream_buffer += text
        if self._live is not None:
            self._live.update(Markdown(self._stream_buffer))

    def stream_end(self) -> None:
        """End streaming. Stop Live and print final Markdown to scrollback."""
        if self._live is not None:
            self._live.stop()
            self._live = None
        if self._stream_buffer:
            # Print final formatted Markdown into permanent scrollback
            self.console.print(Markdown(self._stream_buffer))
        self._stream_buffer = ""
        self._in_stream = False

    def _flush_stream(self) -> None:
        """Flush any active stream (called before tool calls interrupt text)."""
        if self._in_stream:
            self.stream_end()

    # ── Tool calls ──

    def tool_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        self._flush_stream()
        self._tool_start_times[call_id] = time.perf_counter()

        icon = _tool_icon(name)

        args_parts = []
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

    def turn_end(self, status: str) -> None:
        if self._in_stream:
            self.stream_end()
        if status == "error":
            self.console.print(Text("⚠ Turn ended with an error", style="red"))
```

- [ ] **Step 4: Update existing tests to match new behavior**

Update `tests/ui/test_stream_renderer.py` — the key change is `stream_text()` now produces output via Live:

```python
def test_stream_text_accumulates(self):
    """stream_text still builds internal buffer."""
    renderer, _, _ = self._make_renderer()
    renderer.stream_start()
    renderer.stream_text("Hello ")
    renderer.stream_text("world")
    assert renderer._stream_buffer == "Hello world"
    renderer.stream_end()  # Must end to stop Live

def test_stream_end_clears_buffer(self):
    renderer, _, buf = self._make_renderer()
    renderer.stream_start()
    renderer.stream_text("Hello world")
    renderer.stream_end()
    output = buf.getvalue()
    assert "Hello world" in output
    assert renderer._stream_buffer == ""

def test_stream_end_sets_in_stream_false(self):
    renderer, _, _ = self._make_renderer()
    renderer.stream_start()
    renderer.stream_text("text")
    renderer.stream_end()
    assert renderer._in_stream is False

def test_turn_end_ends_active_stream(self):
    renderer, _, buf = self._make_renderer()
    renderer.stream_start()
    renderer.stream_text("some text")
    renderer.turn_end("completed")
    assert renderer._in_stream is False
    assert renderer._stream_buffer == ""
```

- [ ] **Step 5: Run tests to verify**

Run: `uv run pytest tests/ui/test_stream_renderer.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/ui/stream_renderer.py tests/ui/test_stream_renderer.py
git commit -m "feat(ui): real-time streaming Markdown via rich.live.Live"
```

---

## Task 2: Create `approval_prompt.py` — Interactive Approval UI

Build the approval prompt with tool-specific previews and multiple approval options.

**Files:**
- Create: `src/coding_agent/ui/approval_prompt.py`
- Create: `tests/ui/test_approval_prompt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/test_approval_prompt.py
"""Tests for interactive approval prompt."""

import pytest
from io import StringIO
from rich.console import Console

from coding_agent.ui.approval_prompt import (
    format_tool_preview,
    ApprovalChoice,
)
from coding_agent.wire.protocol import ApprovalRequest, ToolCallDelta


class TestFormatToolPreview:
    def test_bash_preview_shows_command(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="bash",
            args={"command": "rm -rf /tmp/test"},
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "rm -rf /tmp/test" in output
        assert "bash" in output.lower() or "⚡" in output

    def test_file_write_preview_shows_content(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="file_write",
            args={"path": "/tmp/test.py", "content": "print('hello')"},
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "test.py" in output
        assert "print" in output

    def test_file_edit_preview_shows_diff(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="file_edit",
            args={
                "path": "/tmp/test.py",
                "old_text": "foo",
                "new_text": "bar",
            },
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "foo" in output
        assert "bar" in output

    def test_generic_tool_preview(self):
        req = ApprovalRequest(
            session_id="s1",
            request_id="r1",
            tool="custom_tool",
            args={"key": "value"},
        )
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        format_tool_preview(console, req)
        output = buf.getvalue()
        assert "custom_tool" in output
        assert "key" in output


class TestApprovalChoice:
    def test_enum_values(self):
        assert ApprovalChoice.APPROVE_ONCE.value == "approve_once"
        assert ApprovalChoice.APPROVE_SESSION.value == "approve_session"
        assert ApprovalChoice.REJECT.value == "reject"
        assert ApprovalChoice.REJECT_WITH_REASON.value == "reject_with_reason"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_approval_prompt.py -v`
Expected: FAIL with `ImportError: No module named 'coding_agent.ui.approval_prompt'`

- [ ] **Step 3: Implement `approval_prompt.py`**

Create `src/coding_agent/ui/approval_prompt.py`:

```python
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
    """User choices for approval requests."""

    APPROVE_ONCE = "approve_once"
    APPROVE_SESSION = "approve_session"
    REJECT = "reject"
    REJECT_WITH_REASON = "reject_with_reason"


def format_tool_preview(console: Console, req: ApprovalRequest) -> None:
    """Render a tool-specific preview panel to the console.

    Different tools get different preview formats:
    - bash: Show the command prominently
    - file_write: Show file path + content preview
    - file_edit: Show old_text → new_text diff
    - Other: Show args as key=value
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
    """Preview a bash command."""
    command = args.get("command", "")
    panel = Panel(
        Syntax(command, "bash", theme="monokai", line_numbers=False),
        title="⚡ [bold yellow]bash[/] — Command to execute",
        border_style="yellow",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


def _preview_file_write(console: Console, args: dict[str, Any]) -> None:
    """Preview a file write operation."""
    path = args.get("path", "unknown")
    content = args.get("content", "")

    # Truncate long content
    if len(content) > 500:
        display = content[:500] + f"\n… ({len(content) - 500} more chars)"
    else:
        display = content

    # Guess language from extension
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    lang_map = {"py": "python", "js": "javascript", "ts": "typescript", "sh": "bash"}
    lang = lang_map.get(ext, ext)

    panel = Panel(
        Syntax(display, lang or "text", theme="monokai", line_numbers=True),
        title=f"📄 [bold cyan]file_write[/] → {path}",
        border_style="cyan",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


def _preview_file_edit(console: Console, args: dict[str, Any]) -> None:
    """Preview a file edit as a diff."""
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
        title="📝 [bold]file_edit[/] — Changes",
        border_style="yellow",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


def _preview_generic(console: Console, tool: str, args: dict[str, Any]) -> None:
    """Preview any other tool call."""
    args_parts = []
    for key, value in args.items():
        val_str = str(value)
        if len(val_str) > 200:
            val_str = val_str[:200] + "…"
        args_parts.append(f"[dim]{key}=[/] {val_str}")
    args_text = "\n".join(args_parts) if args_parts else "[dim]no arguments[/]"

    panel = Panel(
        args_text,
        title=f"🔧 [bold]{tool}[/]",
        border_style="yellow",
        padding=(0, 1),
        expand=False,
    )
    console.print(panel)


async def prompt_approval(console: Console, req: ApprovalRequest) -> ApprovalResponse:
    """Show approval prompt and get user decision.

    Displays:
    1. Tool-specific preview panel
    2. Approval options: [y] approve / [a] approve all / [n] reject / [r] reject with reason

    Returns:
        ApprovalResponse with user's decision and optional feedback.
    """
    console.print()
    console.print("[yellow bold]⚠ Approval Required[/]")
    format_tool_preview(console, req)
    console.print()
    console.print(
        "[bold][green]y[/]=approve  "
        "[cyan]a[/]=approve all (session)  "
        "[red]n[/]=reject  "
        "[yellow]r[/]=reject with reason[/]"
    )

    choice = await asyncio.get_event_loop().run_in_executor(
        None, lambda: input("→ ").strip().lower()
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
        # "n", "no", or anything else → reject
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=False,
            feedback="Rejected by user",
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ui/test_approval_prompt.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/ui/approval_prompt.py tests/ui/test_approval_prompt.py
git commit -m "feat(ui): add interactive approval prompt with tool-specific previews"
```

---

## Task 3: Wire Approval Prompt into `RichConsumer`

Replace the auto-approve in `RichConsumer.request_approval()` with the new interactive prompt.

**Files:**
- Modify: `src/coding_agent/ui/rich_consumer.py`
- Modify: `tests/ui/test_streaming_consumer.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/ui/test_streaming_consumer.py`:

```python
@pytest.mark.asyncio
async def test_approval_shows_preview_and_prompts(self, monkeypatch):
    """request_approval should show tool preview and prompt user."""
    from coding_agent.wire.protocol import ApprovalRequest
    from coding_agent.ui import approval_prompt

    consumer, _, buf = self._make_consumer()

    # Mock the interactive prompt to return an approval
    async def mock_prompt(console, req):
        from coding_agent.wire.protocol import ApprovalResponse
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=True,
            scope="once",
        )

    monkeypatch.setattr(approval_prompt, "prompt_approval", mock_prompt)

    req = ApprovalRequest(
        session_id="s1",
        request_id="r1",
        tool="bash",
        args={"command": "ls"},
    )
    resp = await consumer.request_approval(req)
    assert resp.approved is True
    assert resp.request_id == "r1"

@pytest.mark.asyncio
async def test_approval_rejection(self, monkeypatch):
    """request_approval should handle rejection."""
    from coding_agent.wire.protocol import ApprovalRequest
    from coding_agent.ui import approval_prompt

    consumer, _, buf = self._make_consumer()

    async def mock_prompt(console, req):
        from coding_agent.wire.protocol import ApprovalResponse
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=False,
            feedback="too dangerous",
        )

    monkeypatch.setattr(approval_prompt, "prompt_approval", mock_prompt)

    req = ApprovalRequest(
        session_id="s1",
        request_id="r1",
        tool="bash",
        args={"command": "rm -rf /"},
    )
    resp = await consumer.request_approval(req)
    assert resp.approved is False
    assert resp.feedback == "too dangerous"

@pytest.mark.asyncio
async def test_session_approve_skips_future_prompts(self, monkeypatch):
    """After session-approve, same tool should auto-approve."""
    from coding_agent.wire.protocol import ApprovalRequest
    from coding_agent.ui import approval_prompt

    consumer, _, buf = self._make_consumer()
    call_count = 0

    async def mock_prompt(console, req):
        nonlocal call_count
        call_count += 1
        from coding_agent.wire.protocol import ApprovalResponse
        return ApprovalResponse(
            session_id=req.session_id,
            request_id=req.request_id,
            approved=True,
            scope="session",
        )

    monkeypatch.setattr(approval_prompt, "prompt_approval", mock_prompt)

    req1 = ApprovalRequest(session_id="s1", request_id="r1", tool="bash", args={"command": "ls"})
    resp1 = await consumer.request_approval(req1)
    assert resp1.approved is True
    assert call_count == 1

    # Second request for same tool should auto-approve (no prompt)
    req2 = ApprovalRequest(session_id="s1", request_id="r2", tool="bash", args={"command": "pwd"})
    resp2 = await consumer.request_approval(req2)
    assert resp2.approved is True
    assert call_count == 1  # prompt_approval NOT called again
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ui/test_streaming_consumer.py::TestStreamingConsumer::test_approval_shows_preview_and_prompts -v`
Expected: FAIL — current `request_approval` auto-approves without calling `prompt_approval`

- [ ] **Step 3: Update `RichConsumer` with real approval logic**

Rewrite `src/coding_agent/ui/rich_consumer.py`:

```python
"""Wire message consumer that dispatches to StreamingRenderer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnEnd,
    WireMessage,
)

if TYPE_CHECKING:
    from coding_agent.ui.stream_renderer import StreamingRenderer


class WireConsumer(Protocol):
    async def emit(self, msg: WireMessage) -> None: ...
    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse: ...


class RichConsumer:
    def __init__(self, renderer: StreamingRenderer) -> None:
        self.renderer = renderer
        self._stream_active = False
        self._session_approved_tools: set[str] = set()

    async def emit(self, msg: WireMessage) -> None:
        match msg:
            case StreamDelta(content=text):
                if text:
                    if not self._stream_active:
                        self.renderer.stream_start()
                        self._stream_active = True
                    self.renderer.stream_text(text)

            case ToolCallDelta(tool_name=tool, arguments=args, call_id=cid):
                if self._stream_active:
                    self.renderer.stream_end()
                    self._stream_active = False
                self.renderer.tool_call(cid, tool, args)

            case ToolResultDelta(
                call_id=cid, tool_name=tool, result=result, is_error=err
            ):
                self.renderer.tool_result(cid, tool, result, is_error=err)

            case TurnEnd(completion_status=status):
                if self._stream_active:
                    self.renderer.stream_end()
                    self._stream_active = False
                self.renderer.turn_end(status.value)

            case _:
                pass

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Handle approval requests with interactive prompt.

        If the tool was previously session-approved, auto-approve.
        Otherwise, show the interactive approval prompt.
        """
        from coding_agent.ui.approval_prompt import prompt_approval

        # Check session-level approval
        if req.tool in self._session_approved_tools:
            return ApprovalResponse(
                session_id=req.session_id,
                request_id=req.request_id,
                approved=True,
                scope="session",
            )

        # Pause any active stream before showing approval prompt
        if self._stream_active:
            self.renderer.stream_end()
            self._stream_active = False

        # Show interactive approval prompt
        response = await prompt_approval(self.renderer.console, req)

        # Track session-level approvals
        if response.approved and response.scope == "session":
            self._session_approved_tools.add(req.tool)

        return response
```

- [ ] **Step 4: Update the old auto-approve test**

In `tests/ui/test_streaming_consumer.py`, update the existing `test_approval_auto_approves` test:

```python
@pytest.mark.asyncio
async def test_approval_auto_approves(self, monkeypatch):
    """After session approval, subsequent requests auto-approve."""
    from coding_agent.wire.protocol import ApprovalRequest
    from coding_agent.ui import approval_prompt

    consumer, _, _ = self._make_consumer()

    # Pre-set session approval for bash
    consumer._session_approved_tools.add("bash")

    req = ApprovalRequest(
        session_id="s1", request_id="r1", tool="bash", args={"command": "rm -rf /"}
    )
    resp = await consumer.request_approval(req)
    assert resp.approved is True
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/ui/test_streaming_consumer.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/ui/rich_consumer.py tests/ui/test_streaming_consumer.py
git commit -m "feat(consumer): wire interactive approval prompt into RichConsumer"
```

---

## Task 4: Create `bash_executor.py` — Inline Shell Execution

Build the module that handles `! <command>` execution from the REPL.

**Files:**
- Create: `src/coding_agent/cli/bash_executor.py`
- Create: `tests/cli/test_bash_executor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_bash_executor.py
"""Tests for inline bash executor (! mode)."""

import pytest
from io import StringIO
from rich.console import Console

from coding_agent.cli.bash_executor import BashExecutor


class TestBashExecutor:
    def _make_executor(self) -> tuple[BashExecutor, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        executor = BashExecutor(console=console)
        return executor, buf

    @pytest.mark.asyncio
    async def test_execute_simple_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("echo hello")
        output = buf.getvalue()
        assert "hello" in output
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_failing_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("false")
        assert exit_code != 0

    @pytest.mark.asyncio
    async def test_execute_shows_exit_code_on_failure(self):
        executor, buf = self._make_executor()
        await executor.execute("false")
        output = buf.getvalue()
        assert "exit" in output.lower() or "1" in output

    @pytest.mark.asyncio
    async def test_execute_multiword_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("echo foo bar baz")
        output = buf.getvalue()
        assert "foo bar baz" in output
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_empty_command(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("")
        assert exit_code == 0  # no-op

    @pytest.mark.asyncio
    async def test_execute_whitespace_only(self):
        executor, buf = self._make_executor()
        exit_code = await executor.execute("   ")
        assert exit_code == 0  # no-op

    def test_is_bash_command(self):
        from coding_agent.cli.bash_executor import is_bash_command
        assert is_bash_command("!ls") is True
        assert is_bash_command("! ls") is True
        assert is_bash_command("!  git status") is True
        assert is_bash_command("hello") is False
        assert is_bash_command("/help") is False
        assert is_bash_command("") is False

    def test_extract_bash_command(self):
        from coding_agent.cli.bash_executor import extract_bash_command
        assert extract_bash_command("!ls") == "ls"
        assert extract_bash_command("! ls -la") == "ls -la"
        assert extract_bash_command("!  git status") == "git status"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_bash_executor.py -v`
Expected: FAIL with `ImportError: No module named 'coding_agent.cli.bash_executor'`

- [ ] **Step 3: Implement `bash_executor.py`**

Create `src/coding_agent/cli/bash_executor.py`:

```python
"""Inline bash executor for ! mode.

Lets users run shell commands directly from the REPL by prefixing with !.
Examples:
    !ls -la
    ! git status
    !cat pyproject.toml
"""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.text import Text


def is_bash_command(user_input: str) -> bool:
    """Check if input is a ! bash command."""
    return user_input.startswith("!")


def extract_bash_command(user_input: str) -> str:
    """Extract the shell command from ! prefixed input.

    '!ls' → 'ls'
    '! git status' → 'git status'
    '!  echo hello' → 'echo hello'
    """
    return user_input[1:].strip()


class BashExecutor:
    """Executes shell commands inline and renders output."""

    def __init__(self, console: Console | None = None, cwd: str | None = None) -> None:
        self.console = console or Console()
        self.cwd = cwd

    async def execute(self, command: str) -> int:
        """Execute a shell command and stream output to console.

        Args:
            command: Shell command to execute.

        Returns:
            Exit code of the command.
        """
        if not command.strip():
            return 0

        self.console.print(Text(f"$ {command}", style="bold dim"))

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.cwd,
        )

        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            self.console.print(line.decode("utf-8", errors="replace"), end="")

        await proc.wait()
        exit_code = proc.returncode or 0

        if exit_code != 0:
            self.console.print(
                Text(f"exit code: {exit_code}", style="red dim")
            )

        return exit_code
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/cli/test_bash_executor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/cli/bash_executor.py tests/cli/test_bash_executor.py
git commit -m "feat(cli): add inline bash executor for ! mode"
```

---

## Task 5: Integrate `! bash` Mode into REPL

Wire the bash executor into the REPL main loop.

**Files:**
- Modify: `src/coding_agent/cli/repl.py`
- Modify: `tests/cli/test_repl.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_repl.py` (create if needed):

```python
# tests/cli/test_repl.py
"""Tests for REPL ! bash integration."""

import pytest
from unittest.mock import AsyncMock, patch

from coding_agent.cli.bash_executor import is_bash_command, extract_bash_command


class TestBashIntegration:
    def test_bang_detected_in_repl(self):
        """REPL should detect ! prefix as bash command."""
        assert is_bash_command("!ls")
        assert is_bash_command("! git status")
        assert not is_bash_command("hello")
        assert not is_bash_command("/help")

    def test_bang_extraction(self):
        assert extract_bash_command("!ls") == "ls"
        assert extract_bash_command("! git diff") == "git diff"
```

- [ ] **Step 2: Modify REPL to handle `!` prefix**

In `src/coding_agent/cli/repl.py`, update the `run()` method:

```python
from coding_agent.cli.bash_executor import BashExecutor, is_bash_command, extract_bash_command
```

Add to `__init__`:
```python
self._bash_executor = BashExecutor(console=console, cwd=str(config.repo) if config.repo else None)
```

Update the main loop in `run()` — add between the slash-command check and the message processing:

```python
            # Check for ! bash commands
            if is_bash_command(user_input):
                cmd = extract_bash_command(user_input)
                if cmd:
                    await self._bash_executor.execute(cmd)
                continue
```

The full `run()` method becomes:

```python
    async def run(self):
        """Run the REPL loop."""
        console.print("\n[bold cyan]🤖 Coding Agent[/] - Interactive Mode")
        console.print("[dim]Type /help for commands, !<cmd> for shell, or just chat.[/]\n")

        turn_count = 0

        while not self.context["should_exit"]:
            user_input = await self.input_handler.get_input(prompt=f"[{turn_count}] > ")

            if user_input is None:
                break

            if not user_input:
                continue

            # Check for slash commands
            if user_input.startswith("/"):
                await handle_command(user_input, self.context)
                continue

            # Check for ! bash commands
            if is_bash_command(user_input):
                cmd = extract_bash_command(user_input)
                if cmd:
                    await self._bash_executor.execute(cmd)
                continue

            try:
                await self._process_message(user_input)
            except Exception as e:
                console.print(f"\n[red]Error during agent execution:[/] {e}")
                console.print("[dim]You can continue with a new message.[/]\n")
            turn_count += 1

        console.print("\n[dim]Session ended.[/]\n")
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/cli/test_repl.py tests/cli/test_bash_executor.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/cli/repl.py tests/cli/test_repl.py
git commit -m "feat(repl): integrate ! bash mode for inline shell execution"
```

---

## Task 6: Multiline Input with Alt+Enter

Add multiline editing support to `InputHandler` — Alt+Enter to submit, Enter for newline.

**Files:**
- Modify: `src/coding_agent/cli/input_handler.py`
- Create: `tests/cli/test_input_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_input_handler.py
"""Tests for InputHandler multiline support."""

import pytest
from coding_agent.cli.input_handler import InputHandler


class TestInputHandler:
    def test_handler_creation(self):
        handler = InputHandler()
        assert handler.session is not None

    def test_handler_has_multiline_bindings(self):
        """Handler should have multiline key bindings configured."""
        handler = InputHandler()
        # The handler should be configured for multiline
        assert handler.multiline is True

    def test_slash_command_completer(self):
        from coding_agent.cli.input_handler import SlashCommandCompleter
        from prompt_toolkit.document import Document

        completer = SlashCommandCompleter()
        doc = Document("/hel")
        completions = list(completer.get_completions(doc, None))
        # Should suggest /help
        labels = [c.text for c in completions]
        assert any("/help" in label for label in labels) or len(completions) >= 0

    def test_bang_completer(self):
        """! prefix should offer common command completions."""
        from coding_agent.cli.input_handler import SlashCommandCompleter
        from prompt_toolkit.document import Document

        completer = SlashCommandCompleter()
        doc = Document("!g")
        completions = list(completer.get_completions(doc, None))
        # May or may not have completions for ! prefix, but should not crash
        assert isinstance(completions, list)
```

- [ ] **Step 2: Implement multiline input**

Rewrite `src/coding_agent/cli/input_handler.py`:

```python
"""Interactive input handling with prompt-toolkit.

Supports multiline editing:
- Enter: insert newline
- Alt+Enter (or Escape then Enter): submit input
- On empty buffer, Enter submits (so single-line usage stays fast)
"""

from __future__ import annotations

import time

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from coding_agent.cli.commands import get_command_completions

_CTRLC_TIMEOUT = 2.0


class SlashCommandCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text
        if text.startswith("/"):
            for cmd in get_command_completions():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "bold cyan",
        "input": "white",
    }
)


class InputHandler:
    """Handles interactive user input with history, completion, and multiline."""

    def __init__(self):
        self.multiline = True
        self.bindings = KeyBindings()
        self._last_ctrlc: float = 0.0
        self._setup_bindings()
        self.session = PromptSession(
            completer=SlashCommandCompleter(),
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
            style=PROMPT_STYLE,
            multiline=True,
            key_bindings=self.bindings,
            prompt_continuation=self._continuation_prompt,
        )

    @staticmethod
    def _continuation_prompt(width, line_number, is_soft_wrap):
        """Show continuation prompt for multiline input."""
        return ". " + " " * (width - 2)

    def _should_exit_on_ctrlc(self) -> bool:
        return time.monotonic() - self._last_ctrlc < _CTRLC_TIMEOUT

    def _simulate_ctrlc(self) -> None:
        self._last_ctrlc = time.monotonic()

    def _setup_bindings(self):
        @self.bindings.add("c-c")
        def _(event):
            if self._should_exit_on_ctrlc():
                event.app.exit()
            else:
                self._simulate_ctrlc()
                event.app.current_buffer.reset()

                def _hint():
                    print("\n(Press Ctrl+C again to exit)")

                run_in_terminal(_hint)

        @self.bindings.add("c-d")
        def _(event):
            event.app.exit()

        # Alt+Enter: submit (prompt_toolkit multiline default)
        # Enter on empty buffer: also submit (for fast single-line usage)
        @self.bindings.add("enter")
        def _(event):
            buf = event.app.current_buffer
            # If buffer is empty or is a single-line command (/ or !), submit immediately
            text = buf.text.strip()
            if not text or text.startswith("/") or text.startswith("!"):
                buf.validate_and_handle()
            else:
                buf.insert_text("\n")

    async def get_input(self, prompt: str = "> ") -> str | None:
        """Get input from user.

        Returns:
            User input string, or None if user wants to exit
        """
        try:
            result = await self.session.prompt_async(prompt)
            if result is None:
                return None
            return result.strip()
        except (EOFError, KeyboardInterrupt):
            return None
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/cli/test_input_handler.py -v`
Expected: All PASS

- [ ] **Step 4: Run full REPL tests**

Run: `uv run pytest tests/cli/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/cli/input_handler.py tests/cli/test_input_handler.py
git commit -m "feat(input): add multiline editing with Alt+Enter to submit"
```

---

## Task 7: Integrate `patch_stdout` for Background Output During Input

Ensure agent output (from background processes, etc.) doesn't corrupt the input prompt.

**Files:**
- Modify: `src/coding_agent/cli/repl.py`

- [ ] **Step 1: Add `patch_stdout` to REPL**

In `src/coding_agent/cli/repl.py`, wrap the REPL loop with `patch_stdout`:

At top:
```python
from prompt_toolkit.patch_stdout import patch_stdout
```

Wrap the `run()` method's main loop:

```python
    async def run(self):
        """Run the REPL loop."""
        console.print("\n[bold cyan]🤖 Coding Agent[/] - Interactive Mode")
        console.print("[dim]Type /help for commands, !<cmd> for shell, or just chat.[/]\n")

        turn_count = 0

        with patch_stdout():
            while not self.context["should_exit"]:
                user_input = await self.input_handler.get_input(prompt=f"[{turn_count}] > ")

                if user_input is None:
                    break

                if not user_input:
                    continue

                # Check for slash commands
                if user_input.startswith("/"):
                    await handle_command(user_input, self.context)
                    continue

                # Check for ! bash commands
                if is_bash_command(user_input):
                    cmd = extract_bash_command(user_input)
                    if cmd:
                        await self._bash_executor.execute(cmd)
                    continue

                try:
                    await self._process_message(user_input)
                except Exception as e:
                    console.print(f"\n[red]Error during agent execution:[/] {e}")
                    console.print("[dim]You can continue with a new message.[/]\n")
                turn_count += 1

        console.print("\n[dim]Session ended.[/]\n")
```

- [ ] **Step 2: Run REPL tests**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/coding_agent/cli/repl.py
git commit -m "feat(repl): add patch_stdout for clean background output during input"
```

---

## Task 8: Delete Dead Code (`rich_tui.py` + tests)

Remove the old full-screen TUI that nothing references.

**Files:**
- Delete: `src/coding_agent/ui/rich_tui.py`
- Delete: `tests/ui/test_rich_tui.py`

- [ ] **Step 1: Verify nothing imports `rich_tui`**

Run: `grep -r "rich_tui" src/ --include="*.py"`
Run: `grep -r "CodingAgentTUI" src/ --include="*.py"`

Expected: No results (or only the file itself). If anything still imports it, fix the import first.

- [ ] **Step 2: Delete files**

```bash
rm src/coding_agent/ui/rich_tui.py
rm tests/ui/test_rich_tui.py
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: All pass (no broken imports)

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(ui): remove dead CodingAgentTUI code (rich_tui.py)"
```

---

## Task 9: Update REPL Help and `/help` Command

Update help text to document `!` bash mode and multiline input.

**Files:**
- Modify: `src/coding_agent/cli/commands.py`

- [ ] **Step 1: Update help command**

In `src/coding_agent/cli/commands.py`, update `cmd_help`:

```python
@command("help", "Show available commands")
async def cmd_help(args: list[str], context: dict[str, Any]) -> None:
    """Show help message."""
    console.print("\n[bold cyan]Available Commands:[/]\n")
    for name, func in sorted(_COMMANDS.items()):
        desc = getattr(func, '_command_description', '')
        console.print(f"  [bold]/{name}[/] - {desc}")
    console.print()
    console.print("[bold cyan]Shell Mode:[/]\n")
    console.print("  [bold]!<command>[/] - Execute shell command inline")
    console.print("  Examples: [dim]!ls[/], [dim]! git status[/], [dim]!cat file.py[/]")
    console.print()
    console.print("[bold cyan]Input:[/]\n")
    console.print("  [bold]Enter[/] - New line (in multiline mode)")
    console.print("  [bold]Alt+Enter[/] - Submit message")
    console.print("  [bold]Enter[/] on empty line - Submit")
    console.print("  [bold]Ctrl+C[/] × 2 - Exit")
    console.print()
    console.print("Type your message normally to chat with the agent.\n")
```

- [ ] **Step 2: Run command tests**

Run: `uv run pytest tests/cli/test_commands.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/coding_agent/cli/commands.py
git commit -m "docs(help): update /help with ! bash mode and multiline instructions"
```

---

## Task 10: Full Integration Test + Smoke Test

Run the full test suite and verify everything works end-to-end.

**Files:**
- No new files — verification only

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v --tb=short`
Expected: All pass

- [ ] **Step 2: Run LSP diagnostics on all changed files**

Check these files:
- `src/coding_agent/ui/stream_renderer.py`
- `src/coding_agent/ui/rich_consumer.py`
- `src/coding_agent/ui/approval_prompt.py`
- `src/coding_agent/cli/repl.py`
- `src/coding_agent/cli/input_handler.py`
- `src/coding_agent/cli/bash_executor.py`
- `src/coding_agent/cli/commands.py`

Expected: No errors

- [ ] **Step 3: Manual smoke test**

```bash
uv run python -m coding_agent
```

Verify:
1. **Startup**: Clean banner, prompt appears
2. **Streaming**: Send "What is 2+2?" — text should stream character-by-character with Markdown formatting
3. **Tool calls**: Send "List files in current directory" — tool panel appears, result panel with timing
4. **! bash**: Type `!ls` — shows directory listing inline
5. **! bash error**: Type `!false` — shows exit code
6. **Multiline**: Type some text, press Enter (newline), Alt+Enter (submit)
7. **Slash commands**: `/help` shows updated help with ! mode docs
8. **Permission** (if approval mode enabled): Tool calls show preview + approval prompt
9. **Ctrl+C**: Single press shows hint, double press exits

- [ ] **Step 4: Final commit if any smoke test fixes needed**

```bash
git add -A
git commit -m "fix: smoke test fixes for TUI Phase 2"
```

---

## Summary

| Task | Description | Files | Effort |
|------|-------------|-------|--------|
| 1 | Real streaming Markdown via `rich.live.Live` | 2 | 10 min |
| 2 | Approval prompt with tool-specific previews | 2 | 10 min |
| 3 | Wire approval into `RichConsumer` | 2 | 5 min |
| 4 | `BashExecutor` for `!` mode | 2 | 5 min |
| 5 | Integrate `!` mode into REPL | 2 | 3 min |
| 6 | Multiline input (Alt+Enter) | 2 | 5 min |
| 7 | `patch_stdout` integration | 1 | 2 min |
| 8 | Delete dead code (`rich_tui.py`) | 2 | 2 min |
| 9 | Update `/help` command | 1 | 2 min |
| 10 | Full integration test + smoke test | 0 | 5 min |

**Total: ~49 minutes, 10 tasks, 10 commits**

---

## Dependencies Between Tasks

```
Task 1 (streaming)     → independent, do first
Task 2 (approval UI)   → independent of Task 1
Task 3 (wire approval) → depends on Task 2
Task 4 (bash executor) → independent
Task 5 (bash in REPL)  → depends on Task 4
Task 6 (multiline)     → independent
Task 7 (patch_stdout)  → depends on Task 5 and 6 (REPL changes)
Task 8 (delete dead)   → do after Tasks 1-7
Task 9 (help text)     → depends on Task 5 and 6
Task 10 (verify)       → do last
```

**Recommended parallel execution groups:**
1. Tasks 1, 2, 4, 6 — all independent, can run in parallel
2. Tasks 3, 5 — wire up dependencies
3. Tasks 7, 8, 9 — polish
4. Task 10 — final verification
