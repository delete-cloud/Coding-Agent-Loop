# TUI Tool Collapse & Colored Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Claude Code-style tool result collapsing (search/read ops fold into summary lines) and colored slash command output.

**Architecture:** A streaming state machine in `RichConsumer` buffers consecutive collapsible tool calls (file_read, grep_search, glob_files, grep, glob). When a non-collapsible event arrives (StreamDelta, non-collapsible ToolCallDelta, TurnEnd), it flushes the buffer as a single summary line via a new `StreamingRenderer.collapsed_group()` method. Slash commands switch from plain `print_pt()` to `print_html()` with prompt-toolkit HTML tags for color. Pasted content folding replaces long pastes with `[Pasted text +N lines]` in the input display, expanding before sending to the agent.

**Tech Stack:** Python 3.12, Rich (console output), prompt-toolkit (input/HTML output), pytest + pytest-asyncio (testing)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/coding_agent/ui/collapse.py` | CollapseGroup dataclass + collapsible tool classification logic |
| Modify | `src/coding_agent/ui/rich_consumer.py` | Streaming state machine: buffer collapsible tools, flush on break |
| Modify | `src/coding_agent/ui/stream_renderer.py` | New `collapsed_group()` method for summary line rendering |
| Modify | `src/coding_agent/cli/commands.py` | Colored output via `print_html()` |
| Modify | `src/coding_agent/cli/input_handler.py` | Paste content folding for long inputs |
| Modify | `src/coding_agent/cli/repl.py` | Wire paste expand into message sending |
| Create | `tests/ui/test_collapse.py` | Tests for collapse classification and grouping logic |
| Modify | `tests/ui/test_streaming_consumer.py` | Tests for consumer collapse integration |
| Modify | `tests/ui/test_stream_renderer.py` | Tests for collapsed_group rendering |
| Create | `tests/cli/test_paste_folding.py` | Tests for paste content folding |

---

## Phase 1: Tool Collapse Core

### Task 1: Collapse classification module (`collapse.py`)

**Files:**
- Create: `src/coding_agent/ui/collapse.py`
- Create: `tests/ui/test_collapse.py`

- [ ] **Step 1: Write failing tests for tool classification**

```python
# tests/ui/test_collapse.py
from coding_agent.ui.collapse import is_collapsible, CollapseGroup


class TestIsCollapsible:
    def test_file_read_is_collapsible(self):
        assert is_collapsible("file_read") is True

    def test_grep_search_is_collapsible(self):
        assert is_collapsible("grep_search") is True

    def test_glob_files_is_collapsible(self):
        assert is_collapsible("glob_files") is True

    def test_grep_is_collapsible(self):
        assert is_collapsible("grep") is True

    def test_glob_is_collapsible(self):
        assert is_collapsible("glob") is True

    def test_bash_is_not_collapsible(self):
        assert is_collapsible("bash_run") is False

    def test_file_write_is_not_collapsible(self):
        assert is_collapsible("file_write") is False

    def test_file_replace_is_not_collapsible(self):
        assert is_collapsible("file_replace") is False

    def test_unknown_tool_is_not_collapsible(self):
        assert is_collapsible("custom_tool") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ui/test_collapse.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement collapse classification**

```python
# src/coding_agent/ui/collapse.py
"""Tool call collapse grouping for search/read operations."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

_COLLAPSIBLE_TOOLS: frozenset[str] = frozenset({
    "file_read", "grep_search", "glob_files", "grep", "glob",
})
_SEARCH_TOOLS: frozenset[str] = frozenset({"grep_search", "grep"})
_READ_TOOLS: frozenset[str] = frozenset({"file_read"})
_LIST_TOOLS: frozenset[str] = frozenset({"glob_files", "glob"})


def is_collapsible(tool_name: str) -> bool:
    return tool_name in _COLLAPSIBLE_TOOLS


def classify_tool(tool_name: str) -> str:
    if tool_name in _SEARCH_TOOLS:
        return "search"
    if tool_name in _READ_TOOLS:
        return "read"
    if tool_name in _LIST_TOOLS:
        return "list"
    return "unknown"


@dataclass
class CollapseGroup:
    search_count: int = 0
    read_count: int = 0
    list_count: int = 0
    read_file_paths: list[str] = field(default_factory=list)
    search_patterns: list[str] = field(default_factory=list)
    pending_call_ids: set[str] = field(default_factory=set)
    start_time: float = field(default_factory=time.perf_counter)
    _has_error: bool = False

    def add_tool_call(self, call_id: str, tool_name: str, args: dict[str, object]) -> None:
        category = classify_tool(tool_name)
        if category == "search":
            self.search_count += 1
            pattern = args.get("pattern") or args.get("regex")
            if pattern:
                self.search_patterns.append(str(pattern))
        elif category == "read":
            self.read_count += 1
            path = args.get("path") or args.get("file_path")
            if path:
                self.read_file_paths.append(str(path))
        elif category == "list":
            self.list_count += 1
        self.pending_call_ids.add(call_id)

    def add_tool_result(self, call_id: str, is_error: bool = False) -> None:
        self.pending_call_ids.discard(call_id)
        if is_error:
            self._has_error = True

    def has_call(self, call_id: str) -> bool:
        return call_id in self.pending_call_ids

    @property
    def is_empty(self) -> bool:
        return self.search_count == 0 and self.read_count == 0 and self.list_count == 0

    @property
    def has_error(self) -> bool:
        return self._has_error

    @property
    def duration(self) -> float:
        return time.perf_counter() - self.start_time

    def summary_text(self) -> str:
        parts: list[str] = []
        if self.search_count > 0:
            noun = "pattern" if self.search_count == 1 else "patterns"
            parts.append(f"Searched for {self.search_count} {noun}")
        if self.read_count > 0:
            noun = "file" if self.read_count == 1 else "files"
            parts.append(f"read {self.read_count} {noun}")
        if self.list_count > 0:
            noun = "pattern" if self.list_count == 1 else "patterns"
            parts.append(f"listed {self.list_count} {noun}")
        if not parts:
            return ""
        text = ", ".join(parts)
        return text[0].upper() + text[1:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ui/test_collapse.py -v`

- [ ] **Step 5: Write tests for CollapseGroup behavior**

Add to `tests/ui/test_collapse.py`:

```python
class TestCollapseGroup:
    def test_empty_group(self):
        group = CollapseGroup()
        assert group.is_empty is True
        assert group.summary_text() == ""

    def test_add_search(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep_search", {"pattern": "TODO"})
        assert group.search_count == 1
        assert group.search_patterns == ["TODO"]

    def test_add_read(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "main.py"})
        assert group.read_count == 1
        assert group.read_file_paths == ["main.py"]

    def test_has_call_tracks_pending(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        assert group.has_call("c1") is True
        group.add_tool_result("c1")
        assert group.has_call("c1") is False

    def test_error_tracking(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"path": "a.py"})
        group.add_tool_result("c1", is_error=True)
        assert group.has_error is True

    def test_summary_mixed(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "grep_search", {"pattern": "x"})
        group.add_tool_call("c2", "file_read", {"path": "a.py"})
        group.add_tool_call("c3", "file_read", {"path": "b.py"})
        assert group.summary_text() == "Searched for 1 pattern, read 2 files"

    def test_file_path_arg_variant(self):
        group = CollapseGroup()
        group.add_tool_call("c1", "file_read", {"file_path": "x.py"})
        assert group.read_file_paths == ["x.py"]
```

- [ ] **Step 6: Run and verify, then commit**

Run: `python -m pytest tests/ui/test_collapse.py -v`

```bash
git add src/coding_agent/ui/collapse.py tests/ui/test_collapse.py
git commit -m "feat: add tool collapse classification and grouping module"
```

---

### Task 2: StreamingRenderer collapsed_group() method

**Files:**
- Modify: `src/coding_agent/ui/stream_renderer.py`
- Modify: `tests/ui/test_stream_renderer.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/ui/test_stream_renderer.py`:

```python
class TestCollapsedGroupRendering:
    def _make_renderer(self) -> tuple[StreamingRenderer, Console, StringIO]:
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        return renderer, console, buf

    def test_collapsed_group_shows_summary(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Searched for 2 patterns, read 3 files", duration=1.23, has_error=False)
        output = buf.getvalue()
        assert "Searched for 2 patterns" in output
        assert "read 3 files" in output

    def test_collapsed_group_shows_duration(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.45, has_error=False)
        assert "0.45s" in buf.getvalue()

    def test_collapsed_group_error_indicator(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.1, has_error=True)
        assert "⚠" in buf.getvalue()

    def test_collapsed_group_success_indicator(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.1, has_error=False)
        assert "✓" in buf.getvalue()

    def test_collapsed_group_shows_hint(self):
        renderer, _, buf = self._make_renderer()
        renderer.collapsed_group(summary="Read 1 file", duration=0.1, has_error=False, hint="src/main.py")
        assert "src/main.py" in buf.getvalue()
```

- [ ] **Step 2: Run to verify failure, then implement**

Add to `src/coding_agent/ui/stream_renderer.py` after `tool_result`:

```python
    def collapsed_group(self, summary: str, duration: float, has_error: bool = False, hint: str | None = None) -> None:
        self._flush_stream()
        indicator = Text("⚠ ", style="yellow") if has_error else Text("✓ ", style="green")
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
```

- [ ] **Step 3: Run and commit**

```bash
python -m pytest tests/ui/test_stream_renderer.py -v
git add src/coding_agent/ui/stream_renderer.py tests/ui/test_stream_renderer.py
git commit -m "feat: add collapsed_group rendering to StreamingRenderer"
```

---

### Task 3: RichConsumer collapse state machine

**Files:**
- Modify: `src/coding_agent/ui/rich_consumer.py`
- Modify: `tests/ui/test_streaming_consumer.py`

- [ ] **Step 1: Write failing tests for collapse behavior**

Add to `tests/ui/test_streaming_consumer.py`:

```python
class TestCollapseGrouping:
    def _make_consumer(self):
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = StreamingRenderer(console=console)
        consumer = RichConsumer(renderer)
        return consumer, renderer, buf

    @pytest.mark.asyncio
    async def test_single_read_collapsed(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(ToolCallDelta(tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"))
        await consumer.emit(ToolResultDelta(call_id="c1", tool_name="file_read", result="content"))
        await consumer.emit(TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED))
        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "content" not in output

    @pytest.mark.asyncio
    async def test_consecutive_reads_collapsed(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(ToolCallDelta(tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"))
        await consumer.emit(ToolResultDelta(call_id="c1", tool_name="file_read", result="aaa"))
        await consumer.emit(ToolCallDelta(tool_name="file_read", arguments={"path": "b.py"}, call_id="c2"))
        await consumer.emit(ToolResultDelta(call_id="c2", tool_name="file_read", result="bbb"))
        await consumer.emit(TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED))
        assert "Read 2 files" in buf.getvalue()

    @pytest.mark.asyncio
    async def test_non_collapsible_flushes_group(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(ToolCallDelta(tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"))
        await consumer.emit(ToolResultDelta(call_id="c1", tool_name="file_read", result="aaa"))
        await consumer.emit(ToolCallDelta(tool_name="bash_run", arguments={"command": "ls"}, call_id="c2"))
        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "bash" in output.lower()

    @pytest.mark.asyncio
    async def test_stream_delta_flushes_group(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(ToolCallDelta(tool_name="file_read", arguments={"path": "a.py"}, call_id="c1"))
        await consumer.emit(ToolResultDelta(call_id="c1", tool_name="file_read", result="x"))
        await consumer.emit(StreamDelta(content="Here is my analysis"))
        await consumer.emit(TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED))
        output = buf.getvalue()
        assert "Read 1 file" in output
        assert "Here is my analysis" in output

    @pytest.mark.asyncio
    async def test_bash_renders_full_panel(self):
        consumer, _, buf = self._make_consumer()
        await consumer.emit(ToolCallDelta(tool_name="bash_run", arguments={"command": "ls"}, call_id="c1"))
        await consumer.emit(ToolResultDelta(call_id="c1", tool_name="bash_run", result="file.py"))
        await consumer.emit(TurnEnd(turn_id="t1", completion_status=CompletionStatus.COMPLETED))
        assert "file.py" in buf.getvalue()
```

- [ ] **Step 2: Implement collapse state machine**

Modify `src/coding_agent/ui/rich_consumer.py` — add `_collapse_group` field and update `emit`:

```python
from coding_agent.ui.collapse import CollapseGroup, is_collapsible

# In __init__:
self._collapse_group: CollapseGroup | None = None

# Add flush method:
def _flush_collapse_group(self) -> None:
    group = self._collapse_group
    if group is None or group.is_empty:
        self._collapse_group = None
        return
    hint = None
    if group.read_file_paths:
        hint = group.read_file_paths[-1]
    elif group.search_patterns:
        hint = f'"{group.search_patterns[-1]}"'
    self.renderer.collapsed_group(
        summary=group.summary_text(), duration=group.duration,
        has_error=group.has_error, hint=hint,
    )
    self._collapse_group = None

# Update emit match cases for ToolCallDelta/ToolResultDelta/StreamDelta/TurnEnd
```

- [ ] **Step 3: Run all tests and commit**

```bash
python -m pytest tests/ui/test_streaming_consumer.py -v
git add src/coding_agent/ui/rich_consumer.py tests/ui/test_streaming_consumer.py
git commit -m "feat: implement collapse state machine in RichConsumer"
```

---

## Phase 2: Colored Slash Commands

### Task 4: Colored command output

**Files:**
- Modify: `src/coding_agent/cli/commands.py`

- [ ] **Step 1: Replace print_pt with print_html for styled output**

Update import to include `print_html`, then convert key commands (help, model, tools, skill, mcp) to use `<style fg='cyan'>`, `<b>`, etc.

- [ ] **Step 2: Run tests and commit**

```bash
python -m pytest tests/cli/test_commands.py -v
git add src/coding_agent/cli/commands.py
git commit -m "feat: add colored output to slash commands"
```

---

## Phase 3: Paste Content Folding

### Task 5: Paste folding functions

**Files:**
- Modify: `src/coding_agent/cli/input_handler.py`
- Create: `tests/cli/test_paste_folding.py`

- [ ] **Step 1: Implement `fold_pasted_content()` and `expand_pasted_refs()`**

Add standalone functions to `input_handler.py`. `fold_pasted_content(text, threshold=20)` returns `(folded, {id: original})`. `expand_pasted_refs(text, refs)` substitutes `[Pasted text #N ...]` back.

- [ ] **Step 2: Write tests and verify**

- [ ] **Step 3: Commit**

---

### Task 6: Wire paste folding into REPL

**Files:**
- Modify: `src/coding_agent/cli/input_handler.py` (InputHandler)
- Modify: `src/coding_agent/cli/repl.py`

- [ ] **Step 1: Add `fold_if_long()` and `expand_refs()` to InputHandler**
- [ ] **Step 2: In repl.py, fold after input, expand before `run_turn()`**
- [ ] **Step 3: Run tests and commit**

---

## Phase 4: Integration Verification

### Task 7: End-to-end integration tests

- [ ] **Step 1: Add realistic multi-tool sequence test**
- [ ] **Step 2: Add two-separate-groups test**
- [ ] **Step 3: Run full suite, commit**
