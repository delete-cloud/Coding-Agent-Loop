# Hybrid Streaming: Append + Markdown Re-render

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream LLM output as raw text for visible "typewriter" effect, then replace with rich Markdown rendering at stream end — giving users both real-time feedback and beautiful final output.

**Architecture:** During streaming, text chunks are appended directly to the terminal (current behavior). At `stream_end()`, if the buffer contains Markdown syntax, we use ANSI cursor-up + erase-line sequences (via Rich's `Control` API) to clear the raw output and reprint it as formatted `rich.markdown.Markdown`. This is the same mechanism `rich.Live` uses internally, proven across all modern terminals.

**Tech Stack:** Python, `rich` (Console, Control, ControlType, Markdown, cell_len)

**Terminal Compatibility:** The ANSI sequences used (`CSI n A` = cursor up, `CSI 2 K` = erase line, `CR` = carriage return) are VT100 standard, supported by: macOS Terminal.app, iTerm2, Ghostty, Windows Terminal, Alacritty, Kitty, WezTerm, VS Code terminal, JetBrains terminal, tmux, GNU Screen, GNOME Terminal, Konsole, xterm. For non-terminal output (pipes, files), re-render is skipped automatically via `Console.is_terminal` check.

---

### Task 1: Add terminal line counting

**Files:**
- Modify: `src/coding_agent/ui/stream_renderer.py`
- Test: `tests/ui/test_stream_renderer.py`

Add a method to calculate how many terminal lines a text block occupies, accounting for line wrapping and wide characters (CJK, emoji).

- [ ] **Step 1: Write failing tests for line counting**

```python
# In tests/ui/test_stream_renderer.py

class TestLineCount:
    def _make_renderer(self, width: int = 80) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=width)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_single_line_no_wrap(self):
        renderer, _, _ = self._make_renderer(width=80)
        assert renderer._count_terminal_lines("Hello world") == 1

    def test_multiline(self):
        renderer, _, _ = self._make_renderer(width=80)
        assert renderer._count_terminal_lines("line1\nline2\nline3") == 3

    def test_line_wrapping(self):
        renderer, _, _ = self._make_renderer(width=10)
        # "abcdefghijklmno" = 15 chars, width=10 → wraps to 2 lines
        assert renderer._count_terminal_lines("abcdefghijklmno") == 2

    def test_exact_width_no_extra_wrap(self):
        renderer, _, _ = self._make_renderer(width=10)
        # Exactly 10 chars — should be 1 line, not 2
        assert renderer._count_terminal_lines("abcdefghij") == 1

    def test_wide_characters(self):
        renderer, _, _ = self._make_renderer(width=10)
        # "你好世界你" = 5 CJK chars × 2 cells = 10 cells → 1 line
        assert renderer._count_terminal_lines("你好世界你") == 1

    def test_wide_characters_wrap(self):
        renderer, _, _ = self._make_renderer(width=10)
        # "你好世界你好" = 6 CJK chars × 2 cells = 12 cells → 2 lines
        assert renderer._count_terminal_lines("你好世界你好") == 2

    def test_empty_string(self):
        renderer, _, _ = self._make_renderer(width=80)
        assert renderer._count_terminal_lines("") == 0

    def test_trailing_newline(self):
        renderer, _, _ = self._make_renderer(width=80)
        # "hello\n" → "hello" on line 1, then cursor moves to line 2
        assert renderer._count_terminal_lines("hello\n") == 2

    def test_mixed_wrap_and_newlines(self):
        renderer, _, _ = self._make_renderer(width=10)
        # "abcdefghijklm\nxy" → "abcdefghij" (line 1) + "klm" (line 2, wrapped) + "xy" (line 3, after \n)
        assert renderer._count_terminal_lines("abcdefghijklm\nxy") == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestLineCount -v`
Expected: FAIL with `AttributeError: 'StreamingRenderer' object has no attribute '_count_terminal_lines'`

- [ ] **Step 3: Implement `_count_terminal_lines`**

Add to `stream_renderer.py` imports:
```python
from rich.cells import cell_len
```

Add method to `StreamingRenderer` class:
```python
def _count_terminal_lines(self, text: str) -> int:
    """Count how many terminal lines *text* occupies given current width.

    Accounts for explicit newlines, line wrapping at terminal width,
    and wide characters (CJK / emoji that occupy 2 cells).
    """
    if not text:
        return 0

    width = self.console.size.width
    if width <= 0:
        width = 80  # safe fallback

    total = 0
    for segment in text.split("\n"):
        seg_width = cell_len(segment)
        if seg_width == 0:
            total += 1  # empty line still occupies one line
        else:
            total += (seg_width + width - 1) // width  # ceiling division
    return total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestLineCount -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/ui/stream_renderer.py tests/ui/test_stream_renderer.py
git commit -m "feat(stream): add terminal line counting with wide char support"
```

---

### Task 2: Add cursor-based output clearing

**Files:**
- Modify: `src/coding_agent/ui/stream_renderer.py`
- Test: `tests/ui/test_stream_renderer.py`

Add a method to clear N previously printed lines using ANSI sequences via Rich's `Control` API. This mirrors `rich.live_render.LiveRender.restore_cursor()`.

- [ ] **Step 1: Write failing tests for output clearing**

```python
# In tests/ui/test_stream_renderer.py

class TestClearOutput:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_clear_emits_control_sequences(self):
        renderer, _, buf = self._make_renderer()
        renderer._clear_streamed_output(3)
        output = buf.getvalue()
        # Should contain carriage return (\r) and cursor up (\x1b[1A) and erase line (\x1b[2K)
        assert "\r" in output
        assert "\x1b[1A" in output
        assert "\x1b[2K" in output

    def test_clear_zero_lines_noop(self):
        renderer, _, buf = self._make_renderer()
        renderer._clear_streamed_output(0)
        assert buf.getvalue() == ""

    def test_clear_skipped_when_not_terminal(self):
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=80)
        renderer = StreamingRenderer(console=console)
        renderer._clear_streamed_output(5)
        # Should not emit any control sequences for non-terminal
        assert "\x1b[1A" not in buf.getvalue()

    def test_clear_single_line(self):
        renderer, _, buf = self._make_renderer()
        renderer._clear_streamed_output(1)
        output = buf.getvalue()
        assert "\r" in output
        assert "\x1b[2K" in output
        # Single line: only CR + ERASE, no CURSOR_UP
        assert "\x1b[1A" not in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestClearOutput -v`
Expected: FAIL with `AttributeError: 'StreamingRenderer' object has no attribute '_clear_streamed_output'`

- [ ] **Step 3: Implement `_clear_streamed_output`**

Add to `stream_renderer.py` imports:
```python
from rich.control import Control
from rich.segment import ControlType
```

Add method to `StreamingRenderer` class:
```python
def _clear_streamed_output(self, line_count: int) -> None:
    """Clear *line_count* lines of previously streamed text.

    Uses ANSI cursor-up + erase-line sequences via Rich's Control API.
    Same mechanism as ``rich.live_render.LiveRender.restore_cursor()``.

    Skipped silently when output is not a real terminal (pipes, files).
    """
    if line_count <= 0 or not self.console.is_terminal:
        return

    # Cursor sits at the end of the last printed chunk (no trailing newline).
    # Strategy: CR to start of current line, erase it, then UP+ERASE for
    # each preceding line.
    controls: list[ControlType | tuple[ControlType, int]] = [
        ControlType.CARRIAGE_RETURN,
        (ControlType.ERASE_IN_LINE, 2),  # erase current (last) line
    ]
    for _ in range(line_count - 1):
        controls.append((ControlType.CURSOR_UP, 1))
        controls.append((ControlType.ERASE_IN_LINE, 2))

    self.console.control(Control(*controls))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestClearOutput -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/ui/stream_renderer.py tests/ui/test_stream_renderer.py
git commit -m "feat(stream): add ANSI cursor-based output clearing"
```

---

### Task 3: Add Markdown detection heuristic

**Files:**
- Modify: `src/coding_agent/ui/stream_renderer.py`
- Test: `tests/ui/test_stream_renderer.py`

Detect whether streamed text contains Markdown syntax worth re-rendering. This avoids a visual "flash" (clear + reprint identical text) for plain text responses.

- [ ] **Step 1: Write failing tests for Markdown detection**

```python
# In tests/ui/test_stream_renderer.py
import re

class TestMarkdownDetection:
    def test_code_block(self):
        assert _has_markdown_syntax("here is code:\n```python\nprint('hi')\n```") is True

    def test_heading(self):
        assert _has_markdown_syntax("# Title\nSome text") is True

    def test_bold(self):
        assert _has_markdown_syntax("This is **bold** text") is True

    def test_italic_underscore(self):
        assert _has_markdown_syntax("This is __italic__ text") is True

    def test_link(self):
        assert _has_markdown_syntax("See [docs](https://example.com)") is True

    def test_bullet_list(self):
        assert _has_markdown_syntax("Items:\n* item one\n* item two") is True

    def test_dash_list(self):
        assert _has_markdown_syntax("Items:\n- item one\n- item two") is True

    def test_numbered_list(self):
        assert _has_markdown_syntax("Steps:\n1. first\n2. second") is True

    def test_blockquote(self):
        assert _has_markdown_syntax("> This is a quote") is True

    def test_table(self):
        assert _has_markdown_syntax("| col1 | col2 |\n|------|------|\n| a | b |") is True

    def test_plain_text(self):
        assert _has_markdown_syntax("Hello world, this is plain text.") is False

    def test_plain_with_numbers(self):
        assert _has_markdown_syntax("I have 3 items and 2 tasks.") is False

    def test_empty(self):
        assert _has_markdown_syntax("") is False
```

Note: import `_has_markdown_syntax` from `coding_agent.ui.stream_renderer`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestMarkdownDetection -v`
Expected: FAIL with `ImportError` or `NameError`

- [ ] **Step 3: Implement `_has_markdown_syntax`**

Add module-level function to `stream_renderer.py`:
```python
import re

_MD_PATTERN = re.compile(
    r"```"                # fenced code blocks
    r"|^#{1,6}\s"         # headings
    r"|\*\*[^*]+\*\*"    # bold
    r"|__[^_]+__"         # bold (underscores)
    r"|\[.+?\]\(.+?\)"   # links
    r"|^[*\-]\s"          # bullet lists
    r"|^\d+\.\s"          # numbered lists
    r"|^>\s"              # blockquotes
    r"|^\|.+\|"           # tables
    , re.MULTILINE
)


def _has_markdown_syntax(text: str) -> bool:
    """Return True if *text* contains Markdown syntax worth re-rendering."""
    return bool(_MD_PATTERN.search(text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestMarkdownDetection -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/ui/stream_renderer.py tests/ui/test_stream_renderer.py
git commit -m "feat(stream): add markdown syntax detection heuristic"
```

---

### Task 4: Implement hybrid `stream_end()` with Markdown re-render

**Files:**
- Modify: `src/coding_agent/ui/stream_renderer.py`
- Test: `tests/ui/test_stream_renderer.py`

The core feature: modify `stream_end()` to clear raw text and reprint as formatted Markdown when the buffer contains Markdown syntax. Falls back gracefully for non-terminal, plain text, or errors.

- [ ] **Step 1: Write failing tests for hybrid stream_end**

```python
# In tests/ui/test_stream_renderer.py

class TestHybridStreamEnd:
    def _make_renderer(self, *, force_terminal: bool = True, width: int = 80) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=force_terminal, width=width)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_markdown_content_gets_rerendered(self):
        """stream_end re-renders markdown content via rich.markdown.Markdown."""
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# Hello\n\nThis is **bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        # Markdown renderer produces styled output — the heading should appear
        assert "Hello" in output
        assert "bold" in output

    def test_plain_text_no_rerender(self):
        """Plain text should not trigger re-render (no cursor movement)."""
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Just a simple answer.")
        renderer.stream_end()
        output = buf.getvalue()
        assert "Just a simple answer." in output
        # No cursor-up sequences should appear for plain text
        assert "\x1b[1A" not in output

    def test_non_terminal_no_rerender(self):
        """Non-terminal output should never attempt cursor movement."""
        renderer, _, buf = self._make_renderer(force_terminal=False)
        renderer.stream_start()
        renderer.stream_text("# Heading\n\n**Bold** text.")
        renderer.stream_end()
        output = buf.getvalue()
        # Content should still be there (from streaming), but no ANSI cursor sequences
        assert "Heading" in output
        assert "\x1b[1A" not in output

    def test_empty_stream_no_crash(self):
        """Empty stream should end cleanly without errors."""
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_end()
        assert renderer._in_stream is False

    def test_code_block_gets_rerendered(self):
        """Code blocks should trigger re-render for syntax highlighting."""
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("Here is code:\n```python\nprint('hello')\n```\n")
        renderer.stream_end()
        output = buf.getvalue()
        assert "print" in output

    def test_state_reset_after_rerender(self):
        """After stream_end with re-render, state should be fully reset."""
        renderer, _, buf = self._make_renderer()
        renderer.stream_start()
        renderer.stream_text("# Title\nContent")
        renderer.stream_end()
        assert renderer._in_stream is False
        assert renderer._stream_buffer == ""
        assert renderer._stream_started_output is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ui/test_stream_renderer.py::TestHybridStreamEnd -v`
Expected: Some tests FAIL (plain text test should pass since current behavior already works; markdown tests may fail if re-render logic isn't in place yet — that's fine, the key is the new behavior tests failing)

- [ ] **Step 3: Implement hybrid `stream_end()`**

Update imports at top of `stream_renderer.py`:
```python
from rich.markdown import Markdown
```

Replace the existing `stream_end` method:
```python
def stream_end(self) -> None:
    """End streaming — optionally replace raw text with Markdown rendering.

    When the streamed buffer contains Markdown syntax AND output goes to a
    real terminal, the raw text is cleared and re-rendered as formatted
    ``rich.markdown.Markdown``.  For plain text, non-terminals, or on
    errors the raw output is left untouched.
    """
    if self._stream_started_output and self._stream_buffer:
        should_rerender = (
            self.console.is_terminal
            and _has_markdown_syntax(self._stream_buffer)
        )

        if should_rerender:
            try:
                line_count = self._count_terminal_lines(self._stream_buffer)
                self._clear_streamed_output(line_count)
                self.console.print(Markdown(self._stream_buffer))
            except Exception:
                # If cursor manipulation fails, just add a newline and move on.
                # The raw streamed text is still visible — no data loss.
                self.console.print()
        else:
            # Plain text: just finish with a newline
            self.console.print()
    elif self._stream_started_output:
        self.console.print()

    self._stream_buffer = ""
    self._in_stream = False
    self._stream_started_output = False
```

- [ ] **Step 4: Run ALL stream renderer tests**

Run: `uv run pytest tests/ui/test_stream_renderer.py -v`
Expected: All PASS (including existing tests and new tests)

- [ ] **Step 5: Run full test suite for regression check**

Run: `uv run pytest tests/ui/test_stream_renderer.py tests/ui/test_streaming_consumer.py tests/cli/test_repl.py tests/cli/test_input_handler.py -q`
Expected: All pass (86+ tests)

- [ ] **Step 6: Run lsp_diagnostics on modified file**

Run `lsp_diagnostics` on `src/coding_agent/ui/stream_renderer.py`
Expected: 0 errors

- [ ] **Step 7: Commit**

```bash
git add src/coding_agent/ui/stream_renderer.py tests/ui/test_stream_renderer.py
git commit -m "feat(stream): hybrid streaming — append during stream, Markdown re-render at end"
```

---

### Task 5: Update module docstring and clean up imports

**Files:**
- Modify: `src/coding_agent/ui/stream_renderer.py`

- [ ] **Step 1: Update module docstring**

Replace the module docstring at top of file:
```python
"""Hybrid streaming renderer: append + Markdown re-render.

During streaming, text chunks are appended directly to the terminal for a
visible "typewriter" effect.  When the stream ends and the buffer contains
Markdown syntax, the raw output is cleared (via ANSI cursor-up + erase-line)
and replaced with a fully formatted ``rich.markdown.Markdown`` rendering.

Falls back gracefully:
- Non-terminal output (pipes, files): no cursor manipulation, raw text kept.
- Plain text (no Markdown): no re-render, avoids visual flash.
- Errors during re-render: raw text preserved, newline appended.

Terminal compatibility: uses VT100-standard sequences (CSI n A, CSI 2 K)
supported by all modern terminals including iTerm2, Terminal.app, Ghostty,
Windows Terminal, Alacritty, Kitty, WezTerm, VS Code, JetBrains, tmux,
GNU Screen, xterm, GNOME Terminal, and Konsole.
"""
```

- [ ] **Step 2: Verify imports are clean**

The final import block should be:
```python
from __future__ import annotations

import re
import time
from typing import Any

from rich.cells import cell_len
from rich.console import Console
from rich.control import Control
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import ControlType
from rich.text import Text
```

Remove any unused imports if present.

- [ ] **Step 3: Run lsp_diagnostics**

Expected: 0 errors

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ui/test_stream_renderer.py tests/ui/test_streaming_consumer.py tests/cli/test_repl.py tests/cli/test_input_handler.py -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/ui/stream_renderer.py
git commit -m "docs(stream): update module docstring for hybrid streaming architecture"
```

---

## Appendix: Terminal Compatibility Notes

The ANSI escape sequences used in this implementation:

| Sequence | Name | Code | Support |
|----------|------|------|---------|
| `\r` | Carriage Return | CR | Universal |
| `\x1b[nA` | Cursor Up | CSI n A | VT100+ (all modern terminals) |
| `\x1b[2K` | Erase Line | CSI 2 K | VT100+ (all modern terminals) |

These are the same sequences `rich.Live` uses internally via `LiveRender.restore_cursor()`. If `rich.Live` works in a terminal, our hybrid re-render will too.

**Safety guarantees:**
1. `Console.is_terminal` check — skips cursor manipulation for pipes/files
2. `try/except` around re-render — raw text preserved on any failure
3. No alternate screen buffer needed — works in normal scrollback mode

## Appendix: Line Counting Edge Cases

| Input | Width | Expected Lines | Reasoning |
|-------|-------|---------------|-----------|
| `""` | 80 | 0 | Empty string |
| `"hello"` | 80 | 1 | Fits in one line |
| `"hello\n"` | 80 | 2 | Newline moves to line 2 |
| `"a" * 15` | 10 | 2 | 15/10 = ceil → 2 |
| `"a" * 10` | 10 | 1 | Exactly fills line, no wrap |
| `"你好世界你"` | 10 | 1 | 5 CJK × 2 cells = 10, exactly fits |
| `"你好世界你好"` | 10 | 2 | 6 CJK × 2 cells = 12, wraps |
