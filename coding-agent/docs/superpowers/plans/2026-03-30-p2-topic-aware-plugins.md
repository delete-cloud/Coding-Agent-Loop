# P2: Topic-Aware Plugin Enhancements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MemoryPlugin, MetricsPlugin, and SummarizerPlugin topic-aware so they can scope recall, track costs, and fold context by topic boundaries rather than flat entry counts.

**Architecture:** TopicPlugin (P1) already writes `topic_initial`/`topic_finalized` anchors and emits `on_session_event` notifications. P2 plugins subscribe to these events and use `Entry.meta.topic_id` / anchor types to segment their behavior. No agentkit framework changes needed — all work is in `coding_agent/plugins/`.

**Tech Stack:** Python 3.11+, pytest, agentkit (Entry, Tape, HookRuntime)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/coding_agent/plugins/memory.py` | Modify | Add topic-scoped recall: filter memories by current topic's file tags |
| `tests/coding_agent/plugins/test_memory.py` | Modify | New tests for topic-scoped grounding |
| `src/coding_agent/plugins/metrics.py` | Modify | Add per-topic metrics accumulation via `on_session_event` |
| `tests/coding_agent/plugins/test_metrics.py` | Modify | New tests for topic-level cost tracking |
| `src/coding_agent/plugins/summarizer.py` | Modify | Replace entry-count windowing with topic-boundary windowing |
| `tests/coding_agent/plugins/test_summarizer.py` | Modify | New tests for topic-aware handoff |

No new files. No config changes (plugin hook registration updates are internal).

---

## Conventions & Helpers

All three tasks share these test helpers. Each test file already has its own `FakePipelineContext` or similar — extend those rather than creating a shared module.

```python
# Reusable entry builders (copy into each test file that needs them)
def _make_user_msg(content: str) -> Entry:
    return Entry(kind="message", payload={"role": "user", "content": content})

def _make_assistant_msg(content: str) -> Entry:
    return Entry(kind="message", payload={"role": "assistant", "content": content})

def _make_tool_call(name: str, arguments: dict | None = None) -> Entry:
    return Entry(
        kind="tool_call",
        payload={"id": "tc1", "name": name, "arguments": arguments or {}, "role": "assistant"},
    )

def _make_topic_initial(topic_id: str, topic_number: int = 1) -> Entry:
    return Entry(
        kind="anchor",
        payload={"content": f"Topic #{topic_number}"},
        meta={"anchor_type": "topic_initial", "topic_id": topic_id, "topic_number": topic_number},
    )

def _make_topic_finalized(topic_id: str, files: list[str] | None = None) -> Entry:
    return Entry(
        kind="anchor",
        payload={"content": f"Topic involved files: {', '.join(files or [])}"},
        meta={"anchor_type": "topic_finalized", "topic_id": topic_id, "files": files or []},
    )
```

---

### Task 1: MemoryPlugin — Topic-Scoped Recall

**Files:**
- Modify: `src/coding_agent/plugins/memory.py`
- Modify: `tests/coding_agent/plugins/test_memory.py`

**Design:** Currently `build_context` injects top-N memories by importance across all history. After this task, if the current topic has file tags (from `TopicPlugin`'s state in `ctx.plugin_states["topic"]`), `build_context` filters memories to those whose tags overlap with the topic's recent files. Falls back to current behavior if no topic context is available.

Key change: `MemoryPlugin` needs access to `ctx` in `build_context`, but the current hook signature only receives `tape`. We solve this by also subscribing to `on_checkpoint` (which receives `ctx`) to cache the current topic's file set, then using that cache in `build_context`.

- [ ] **Step 1: Write test — topic-scoped recall filters by file tags**

Add to `tests/coding_agent/plugins/test_memory.py`:

```python
def _make_topic_initial(topic_id: str, topic_number: int = 1) -> Entry:
    return Entry(
        kind="anchor",
        payload={"content": f"Topic #{topic_number}"},
        meta={"anchor_type": "topic_initial", "topic_id": topic_id, "topic_number": topic_number},
    )


class TestMemoryTopicScopedRecall:
    """P2: build_context filters memories by current topic's file tags."""

    def test_memories_filtered_by_topic_files(self):
        plugin = MemoryPlugin()
        # Simulate two memories from different topics
        plugin._memories = [
            {"summary": "Fixed auth bug", "tags": ["src/auth.py", "file_read"], "importance": 0.8},
            {"summary": "Fixed UI layout", "tags": ["src/ui/app.tsx", "file_read"], "importance": 0.9},
        ]
        # Set topic context with auth files
        plugin._topic_file_tags = {"src/auth.py", "src/auth_utils.py"}

        tape = Tape()
        result = plugin.build_context(tape=tape)

        # Only the auth memory should be injected
        assert len(result) == 1
        assert "auth" in result[0]["content"]

    def test_fallback_to_importance_when_no_topic_context(self):
        plugin = MemoryPlugin()
        plugin._memories = [
            {"summary": "Fixed auth", "tags": ["src/auth.py"], "importance": 0.8},
            {"summary": "Fixed UI", "tags": ["src/ui/app.tsx"], "importance": 0.9},
        ]
        # No topic context set
        plugin._topic_file_tags = set()

        tape = Tape()
        result = plugin.build_context(tape=tape)

        # Falls back to importance-sorted top-N (both included)
        assert len(result) == 2

    def test_topic_files_updated_from_checkpoint(self):
        plugin = MemoryPlugin()
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "fix auth"}))
        tape.append(
            Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/auth.py"}})
        )

        class FakeCtx:
            def __init__(self, tape):
                self.tape = tape
                self.plugin_states = {
                    "topic": {"current_topic_id": "topic-abc", "topic_count": 1}
                }

        ctx = FakeCtx(tape)
        plugin.on_checkpoint(ctx=ctx)

        assert "src/auth.py" in plugin._topic_file_tags

    def test_tag_overlap_includes_partial_path_match(self):
        """Memory tagged with src/auth.py matches topic working on src/auth.py."""
        plugin = MemoryPlugin()
        plugin._memories = [
            {"summary": "Auth fix", "tags": ["src/auth.py"], "importance": 0.5},
            {"summary": "DB fix", "tags": ["src/db.py"], "importance": 0.5},
        ]
        plugin._topic_file_tags = {"src/auth.py", "tests/test_auth.py"}

        tape = Tape()
        result = plugin.build_context(tape=tape)

        assert len(result) == 1
        assert "Auth" in result[0]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_memory.py::TestMemoryTopicScopedRecall -v`

Expected: FAIL — `_topic_file_tags` attribute doesn't exist, `on_checkpoint` hook not registered.

- [ ] **Step 3: Implement topic-scoped recall**

Modify `src/coding_agent/plugins/memory.py`:

1. Add `_topic_file_tags: set[str]` to `__init__`:

```python
def __init__(self, max_grounding: int = 5) -> None:
    self._max_grounding = max_grounding
    self._memories: list[dict[str, Any]] = []
    self._topic_file_tags: set[str] = set()
```

2. Register `on_checkpoint` in `hooks()`:

```python
def hooks(self) -> dict[str, Callable[..., Any]]:
    return {
        "build_context": self.build_context,
        "on_turn_end": self.on_turn_end,
        "on_checkpoint": self.on_checkpoint,
        "mount": self.do_mount,
    }
```

3. Add `on_checkpoint` method to extract file paths from recent tool_calls:

```python
def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
    """Cache current topic's file tags for scoped recall."""
    if ctx is None:
        return
    entries = (
        ctx.tape.windowed_entries()
        if hasattr(ctx.tape, "windowed_entries")
        else list(ctx.tape)
    )
    files: set[str] = set()
    for entry in entries:
        if entry.kind == "tool_call":
            args = entry.payload.get("arguments")
            if isinstance(args, dict):
                for key in ("path", "file", "filename", "file_path"):
                    val = args.get(key, "")
                    if val and isinstance(val, str):
                        files.add(val)
    self._topic_file_tags = files
```

4. Modify `build_context` to filter by topic files when available:

```python
def build_context(
    self, tape: Tape | None = None, **kwargs: Any
) -> list[dict[str, Any]]:
    """Grounding mode: inject relevant memories as system messages.

    If topic file tags are available, filter memories to those with
    overlapping tags. Falls back to importance-sorted top-N otherwise.
    """
    if not self._memories:
        return []

    if self._topic_file_tags:
        relevant = [
            m for m in self._memories
            if self._tags_overlap(m.get("tags", []), self._topic_file_tags)
        ]
        if relevant:
            sorted_memories = sorted(
                relevant, key=lambda m: m.get("importance", 0.5), reverse=True
            )
        else:
            sorted_memories = sorted(
                self._memories, key=lambda m: m.get("importance", 0.5), reverse=True
            )
    else:
        sorted_memories = sorted(
            self._memories, key=lambda m: m.get("importance", 0.5), reverse=True
        )

    top = sorted_memories[: self._max_grounding]

    grounding_messages = []
    for mem in top:
        content = f"[Memory] {mem['summary']}"
        if mem.get("tags"):
            content += f" (tags: {', '.join(mem['tags'])})"
        grounding_messages.append({"role": "system", "content": content})

    return grounding_messages

def _tags_overlap(self, memory_tags: list[str], topic_files: set[str]) -> bool:
    """Check if any memory tag overlaps with topic file paths."""
    for tag in memory_tags:
        if tag in topic_files:
            return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_memory.py -v`

Expected: All tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/memory.py tests/coding_agent/plugins/test_memory.py
git commit -m "feat(memory): topic-scoped recall filters memories by current topic's file tags"
```

---

### Task 2: MetricsPlugin — Topic-Level Cost Tracking

**Files:**
- Modify: `src/coding_agent/plugins/metrics.py`
- Modify: `tests/coding_agent/plugins/test_metrics.py`

**Design:** Subscribe to `on_session_event` to detect `topic_start`/`topic_end`. When a topic ends, snapshot accumulated metrics (steps, tool_calls, time) into a per-topic archive. Expose `get_topic_metrics()` for reporting. Session-level metrics continue working unchanged.

- [ ] **Step 1: Write test — topic metrics accumulated and archived on topic_end**

Add to `tests/coding_agent/plugins/test_metrics.py`:

```python
class TestSessionMetricsTopicTracking:
    """P2: per-topic metrics via on_session_event."""

    def test_hooks_include_on_session_event(self) -> None:
        plugin = SessionMetricsPlugin()
        hooks = plugin.hooks()
        assert "on_session_event" in hooks

    def test_topic_start_sets_current_topic(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "topic-abc"})
        assert plugin._current_topic_id == "topic-abc"

    def test_topic_end_archives_metrics(self) -> None:
        plugin = SessionMetricsPlugin()
        # Start a topic
        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "topic-abc"})

        # Simulate some work via on_checkpoint
        tape = Tape()
        tape.append(_make_tool_call("file_read", {"path": "/a.py"}))
        tape.append(_make_tool_result())
        tape.append(_make_tool_call("grep", {"pattern": "foo"}))
        tape.append(_make_tool_result())
        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)

        # End the topic
        plugin.on_session_event(event_type="topic_end", payload={"topic_id": "topic-abc"})

        # Archived metrics should exist
        topic_metrics = plugin.get_topic_metrics("topic-abc")
        assert topic_metrics is not None
        assert topic_metrics["steps_count"] == 2
        assert topic_metrics["tool_calls"]["file_read"] == 1
        assert topic_metrics["tool_calls"]["grep"] == 1

    def test_multiple_topics_tracked_independently(self) -> None:
        plugin = SessionMetricsPlugin()

        # Topic 1
        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "t1"})
        tape = Tape()
        tape.append(_make_tool_call("file_read"))
        tape.append(_make_tool_result())
        ctx = FakePipelineContext(tape=tape)
        plugin.on_checkpoint(ctx=ctx)
        plugin.on_session_event(event_type="topic_end", payload={"topic_id": "t1"})

        # Topic 2
        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "t2"})
        tape2 = Tape()
        for _ in range(5):
            tape2.append(_make_tool_call("grep"))
            tape2.append(_make_tool_result())
        ctx2 = FakePipelineContext(tape=tape2)
        plugin.on_checkpoint(ctx=ctx2)
        plugin.on_session_event(event_type="topic_end", payload={"topic_id": "t2"})

        t1 = plugin.get_topic_metrics("t1")
        t2 = plugin.get_topic_metrics("t2")
        assert t1["steps_count"] == 1
        assert t2["steps_count"] == 5

    def test_get_all_topic_metrics(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_session_event(event_type="topic_start", payload={"topic_id": "t1"})
        plugin.on_session_event(event_type="topic_end", payload={"topic_id": "t1"})

        all_metrics = plugin.get_all_topic_metrics()
        assert "t1" in all_metrics

    def test_unknown_topic_returns_none(self) -> None:
        plugin = SessionMetricsPlugin()
        assert plugin.get_topic_metrics("nonexistent") is None

    def test_non_topic_events_ignored(self) -> None:
        plugin = SessionMetricsPlugin()
        plugin.on_session_event(event_type="handoff", payload={"reason": "window"})
        assert plugin._current_topic_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_metrics.py::TestSessionMetricsTopicTracking -v`

Expected: FAIL — `on_session_event` not in hooks, `_current_topic_id` doesn't exist.

- [ ] **Step 3: Implement topic-level metrics**

Modify `src/coding_agent/plugins/metrics.py`:

1. Add topic tracking state to `__init__`:

```python
def __init__(self) -> None:
    self._turn_start: float | None = None
    self._steps_count: int = 0
    self._tool_calls: dict[str, int] = defaultdict(int)
    self._api_calls: int = 0
    self._api_latency_total: float = 0.0
    # Topic tracking
    self._current_topic_id: str | None = None
    self._topic_metrics: dict[str, dict[str, Any]] = {}
```

2. Register `on_session_event` in `hooks()`:

```python
def hooks(self) -> dict[str, Callable[..., Any]]:
    return {
        "on_checkpoint": self.on_checkpoint,
        "on_session_event": self.on_session_event,
    }
```

3. Add `on_session_event` method:

```python
def on_session_event(
    self, event_type: str = "", payload: dict[str, Any] | None = None, **kwargs: Any
) -> None:
    payload = payload or {}
    if event_type == "topic_start":
        self._current_topic_id = payload.get("topic_id")
    elif event_type == "topic_end":
        topic_id = payload.get("topic_id")
        if topic_id:
            self._topic_metrics[topic_id] = {
                "steps_count": self._steps_count,
                "tool_calls": dict(self._tool_calls),
                "topic_id": topic_id,
            }
```

4. Add query methods:

```python
def get_topic_metrics(self, topic_id: str) -> dict[str, Any] | None:
    return self._topic_metrics.get(topic_id)

def get_all_topic_metrics(self) -> dict[str, dict[str, Any]]:
    return dict(self._topic_metrics)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_metrics.py -v`

Expected: All tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/metrics.py tests/coding_agent/plugins/test_metrics.py
git commit -m "feat(metrics): topic-level cost tracking via on_session_event"
```

---

### Task 3: SummarizerPlugin — Topic-Aware Handoff

**Files:**
- Modify: `src/coding_agent/plugins/summarizer.py`
- Modify: `tests/coding_agent/plugins/test_summarizer.py`

**Design:** Replace entry-count windowing with topic-boundary windowing. When tape exceeds `max_entries`, find the most recent `topic_finalized` anchor and use it as the handoff boundary — all entries before that finalized topic get folded behind the anchor. Falls back to current entry-count strategy if no topic boundaries exist.

This is the most impactful change: instead of blindly cutting the oldest N entries, it preserves the active topic's full context and only folds completed topics.

- [ ] **Step 1: Write test — topic-boundary windowing**

Add to `tests/coding_agent/plugins/test_summarizer.py`:

```python
def _make_topic_initial(topic_id: str, topic_number: int = 1) -> Entry:
    return Entry(
        kind="anchor",
        payload={"content": f"Topic #{topic_number}"},
        meta={"anchor_type": "topic_initial", "topic_id": topic_id, "topic_number": topic_number},
    )


def _make_topic_finalized(topic_id: str, files: list[str] | None = None) -> Entry:
    return Entry(
        kind="anchor",
        payload={"content": f"Topic involved files: {', '.join(files or [])}"},
        meta={"anchor_type": "topic_finalized", "topic_id": topic_id, "files": files or []},
    )


class TestSummarizerTopicAwareHandoff:
    """P2: topic-boundary windowing instead of entry-count truncation."""

    def test_folds_at_topic_boundary_when_over_max(self):
        """If tape has completed topics + active topic exceeding max, fold at last topic_finalized."""
        plugin = SummarizerPlugin(max_entries=10, keep_recent=5)
        tape = Tape()

        # Completed topic 1: 8 entries
        tape.append(_make_topic_initial("t1", 1))
        for i in range(6):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"t1 msg {i}"}))
        tape.append(_make_topic_finalized("t1", files=["src/auth.py"]))

        # Active topic 2: 6 entries
        tape.append(_make_topic_initial("t2", 2))
        for i in range(5):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"t2 msg {i}"}))

        # Total: 14 entries > max_entries=10
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result

        # Split should be at or after topic_finalized for t1
        # All of t1 (8 entries) should be folded
        assert split_point == 8  # after topic_finalized
        assert anchor.kind == "anchor"
        assert anchor.meta.get("anchor_type") == "handoff"

    def test_no_fold_when_under_max(self):
        plugin = SummarizerPlugin(max_entries=50)
        tape = Tape()
        tape.append(_make_topic_initial("t1"))
        for i in range(5):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"msg {i}"}))
        result = plugin.resolve_context_window(tape=tape)
        assert result is None

    def test_fallback_to_entry_count_when_no_topics(self):
        """If no topic boundaries exist, fall back to entry-count truncation."""
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        for i in range(20):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"msg {i}"}))
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        # Fallback: split at len - keep_recent
        assert split_point == 17  # 20 - 3

    def test_multiple_completed_topics_folds_all(self):
        """Multiple completed topics → fold all of them, keep only the active one."""
        plugin = SummarizerPlugin(max_entries=10)
        tape = Tape()

        # Topic 1: 4 entries
        tape.append(_make_topic_initial("t1", 1))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "t1 work"}))
        tape.append(Entry(kind="message", payload={"role": "assistant", "content": "t1 done"}))
        tape.append(_make_topic_finalized("t1"))

        # Topic 2: 4 entries
        tape.append(_make_topic_initial("t2", 2))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "t2 work"}))
        tape.append(Entry(kind="message", payload={"role": "assistant", "content": "t2 done"}))
        tape.append(_make_topic_finalized("t2"))

        # Topic 3 (active): 5 entries
        tape.append(_make_topic_initial("t3", 3))
        for i in range(4):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"t3 msg {i}"}))

        # Total: 13 > max=10. Should fold at t2's finalized (index 7, so split_point=8)
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert split_point == 8  # after t2's topic_finalized

    def test_handoff_anchor_contains_topic_summary(self):
        plugin = SummarizerPlugin(max_entries=5)
        tape = Tape()
        tape.append(_make_topic_initial("t1", 1))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "fix auth bug"}))
        tape.append(_make_topic_finalized("t1", files=["src/auth.py"]))
        tape.append(_make_topic_initial("t2", 2))
        for i in range(5):
            tape.append(Entry(kind="message", payload={"role": "user", "content": f"t2 {i}"}))

        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        _, anchor = result
        assert "topic" in anchor.payload.get("content", "").lower() or "summarized" in anchor.payload.get("content", "").lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_summarizer.py::TestSummarizerTopicAwareHandoff -v`

Expected: FAIL — current `resolve_context_window` uses entry-count, not topic boundaries.

- [ ] **Step 3: Implement topic-aware handoff**

Replace `resolve_context_window` in `src/coding_agent/plugins/summarizer.py`:

```python
def resolve_context_window(
    self, tape: Tape | None = None, **kwargs: Any
) -> tuple[int, Entry] | None:
    """Determine context window boundaries.

    Strategy:
    1. If tape has topic_finalized anchors and exceeds max_entries,
       fold at the last topic_finalized boundary.
    2. Otherwise, fall back to entry-count truncation (keep_recent).

    Returns (window_start_index, summary_anchor_entry) or None.
    """
    if tape is None:
        return None

    visible = tape.windowed_entries() if hasattr(tape, "windowed_entries") else list(tape)
    if len(visible) <= self._max_entries:
        return None

    # Strategy 1: find the last topic_finalized anchor
    last_finalized_idx = self._find_last_finalized(visible)
    if last_finalized_idx is not None:
        split_point = last_finalized_idx + 1  # fold everything up to and including the finalized anchor
        old_entries = visible[:split_point]
        summary_anchor = self._build_topic_summary(old_entries)
        return (split_point, summary_anchor)

    # Strategy 2: fallback to entry-count truncation
    split_point = len(visible) - self._keep_recent
    old_entries = visible[:split_point]
    summary_anchor = self._build_entry_summary(old_entries)
    return (split_point, summary_anchor)

def _find_last_finalized(self, entries: list[Entry]) -> int | None:
    """Find index of the last topic_finalized anchor in entries."""
    for i in range(len(entries) - 1, -1, -1):
        if (
            entries[i].kind == "anchor"
            and entries[i].meta.get("anchor_type") == "topic_finalized"
        ):
            return i
    return None

def _build_topic_summary(self, old_entries: list[Entry]) -> Entry:
    """Build a handoff anchor summarizing folded topic entries."""
    topic_ids = []
    for e in old_entries:
        tid = e.meta.get("topic_id")
        if tid and tid not in topic_ids:
            topic_ids.append(tid)

    files: list[str] = []
    for e in old_entries:
        if e.meta.get("anchor_type") == "topic_finalized":
            files.extend(e.meta.get("files", []))

    topic_count = len(topic_ids) or 1
    summary_text = f"[Summarized {len(old_entries)} entries from {topic_count} completed topic(s)]"
    if files:
        summary_text += f"\nFiles involved: {', '.join(sorted(set(files))[:10])}"

    return Entry(
        kind="anchor",
        payload={"content": summary_text},
        meta={
            "anchor_type": "handoff",
            "source_entry_count": len(old_entries),
            "folded_topics": topic_ids,
        },
    )

def _build_entry_summary(self, old_entries: list[Entry]) -> Entry:
    """Build a handoff anchor from raw entry list (fallback)."""
    summary_parts = []
    for entry in old_entries:
        if entry.kind == "message":
            role = entry.payload.get("role", "?")
            content = entry.payload.get("content", "")
            preview = content[:100] + "..." if len(content) > 100 else content
            summary_parts.append(f"[{role}] {preview}")
        elif entry.kind == "tool_call":
            name = entry.payload.get("name", "?")
            summary_parts.append(f"[tool_call] {name}")
        elif entry.kind == "tool_result":
            summary_parts.append("[tool_result] ...")

    summary_text = (
        f"[Summarized {len(old_entries)} earlier entries]\n"
        + "\n".join(summary_parts[-10:])
    )

    return Entry(
        kind="anchor",
        payload={"content": summary_text},
        meta={
            "anchor_type": "handoff",
            "source_entry_count": len(old_entries),
        },
    )
```

Also remove the old `summarize_context` legacy method — it is no longer needed since Pipeline already falls back to it only when `resolve_context_window` returns None, and we always return from `resolve_context_window` now. **Actually, keep it** for backward compat with any external code — just leave it as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_summarizer.py -v`

Expected: All tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/summarizer.py tests/coding_agent/plugins/test_summarizer.py
git commit -m "feat(summarizer): topic-aware handoff folds at topic boundaries instead of entry count"
```

---

### Task 4: Full Suite Verification

**Files:** None (read-only verification)

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ -x -q`

Expected: All tests PASS (826+ existing + ~17 new ≈ 843+).

- [ ] **Step 2: Run mypy on changed files**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run mypy src/coding_agent/plugins/memory.py src/coding_agent/plugins/metrics.py src/coding_agent/plugins/summarizer.py --ignore-missing-imports`

Expected: No new errors in changed files.

- [ ] **Step 3: Verify topic → summarizer interaction end-to-end**

Run a quick smoke test that creates a tape with topic transitions and verifies the summarizer folds correctly:

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run python -c "
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from coding_agent.plugins.summarizer import SummarizerPlugin

tape = Tape()
# Topic 1
tape.append(Entry(kind='anchor', payload={'content': 'T1'}, meta={'anchor_type': 'topic_initial', 'topic_id': 't1'}))
for i in range(10):
    tape.append(Entry(kind='message', payload={'role': 'user', 'content': f't1-{i}'}))
tape.append(Entry(kind='anchor', payload={'content': 'T1 done'}, meta={'anchor_type': 'topic_finalized', 'topic_id': 't1', 'files': ['a.py']}))

# Topic 2 (active)
tape.append(Entry(kind='anchor', payload={'content': 'T2'}, meta={'anchor_type': 'topic_initial', 'topic_id': 't2'}))
for i in range(5):
    tape.append(Entry(kind='message', payload={'role': 'user', 'content': f't2-{i}'}))

plugin = SummarizerPlugin(max_entries=10)
result = plugin.resolve_context_window(tape=tape)
assert result is not None, 'Expected windowing'
split, anchor = result
print(f'Split at {split}, anchor: {anchor.meta}')
print(f'Summary: {anchor.payload[\"content\"][:100]}')
print('OK')
"
```

Expected: Prints split point at index 12 (after topic_finalized), anchor with `folded_topics: ['t1']`, "OK".

---

## Summary

| Task | Plugin | Change | New Tests |
|------|--------|--------|-----------|
| 1 | MemoryPlugin | Topic-scoped recall via file tag filtering | 4 |
| 2 | MetricsPlugin | Per-topic cost tracking via on_session_event | 7 |
| 3 | SummarizerPlugin | Topic-boundary windowing replaces entry-count truncation | 5 |
| 4 | — | Full suite verification | — |

Tasks 1, 2, and 3 are independent and can be executed in parallel. Task 4 runs after all three complete.
