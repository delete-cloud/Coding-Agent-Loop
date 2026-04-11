# P1: Topic 中间粒度层 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 agentkit 和 coding-agent 之间建立 Topic 中间粒度——让系统能感知"任务边界"，实现 session 级事件通知、topic-aware 的上下文折叠、以及基于文件路径的主题检测。

**Architecture:** agentkit 层新增 `on_session_event` hook 和 ContextBuilder anchor 折叠能力（纯机制，不含策略）。coding-agent 层新增 `TopicPlugin`，利用 tool_call 中的文件路径变化做零成本主题边界检测，写入 `topic_initial` / `topic_finalized` anchor。

**Tech Stack:** Python 3.14, dataclasses, agentkit hook system, pytest

**前置依赖:** P0 已完成（Entry.meta ✅、append-only 存储 ✅、resolve_context_window ✅）

---

## 文件结构

| 操作 | 文件路径 | 职责 |
|------|---------|------|
| Modify | `src/agentkit/runtime/hookspecs.py` | 新增 `on_session_event` hookspec |
| Modify | `src/agentkit/context/builder.py` | anchor 折叠：根据 `meta.anchor_type` 差异化渲染 |
| Create | `src/coding_agent/plugins/topic.py` | TopicPlugin：主题检测 + 生命周期管理 |
| Modify | `src/coding_agent/__main__.py` | 注册 TopicPlugin |
| Modify | `src/coding_agent/agent.toml` | 启用 topic plugin |
| Create | `tests/coding_agent/plugins/test_topic.py` | TopicPlugin 测试 |
| Modify | `tests/agentkit/runtime/test_hookspecs.py` | 更新 hook 数量断言 |
| Modify | `tests/agentkit/context/test_builder.py` | anchor 折叠测试 |

---

## Task 1: on_session_event hookspec（agentkit 层）

**Files:**
- Modify: `src/agentkit/runtime/hookspecs.py:27-91`
- Modify: `tests/agentkit/runtime/test_hookspecs.py:6-21`

- [ ] **Step 1: 更新 hookspecs 测试——期望 13 个 hooks**

```python
# tests/agentkit/runtime/test_hookspecs.py
# 修改 test_all_12_hooks_defined → test_all_13_hooks_defined

def test_all_13_hooks_defined(self):
    expected = {
        "provide_storage",
        "get_tools",
        "provide_llm",
        "approve_tool_call",
        "summarize_context",
        "resolve_context_window",
        "on_error",
        "mount",
        "on_checkpoint",
        "build_context",
        "on_turn_end",
        "execute_tool",
        "on_session_event",
    }
    assert set(HOOK_SPECS.keys()) == expected
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/python -m pytest tests/agentkit/runtime/test_hookspecs.py::TestHookSpecs::test_all_13_hooks_defined -v`
Expected: FAIL — `on_session_event` not in HOOK_SPECS

- [ ] **Step 3: 添加 on_session_event hookspec**

在 `src/agentkit/runtime/hookspecs.py` 的 `HOOK_SPECS` dict 中，`"execute_tool"` 之后添加：

```python
"on_session_event": HookSpec(
    name="on_session_event",
    is_observer=True,
    doc="Observer: notified on session-level events (topic_start, topic_end, handoff, etc). "
        "Receives event_type: str and payload: dict. Cannot affect pipeline flow.",
),
```

同时更新文件顶部 docstring：`"""Hook specifications — metadata for the 13 agentkit hooks.`

- [ ] **Step 4: 添加 hookspec 属性测试**

在 `tests/agentkit/runtime/test_hookspecs.py` 的 `TestHookSpecs` 类末尾添加：

```python
def test_on_session_event_is_observer(self):
    spec = HOOK_SPECS["on_session_event"]
    assert spec.is_observer is True
    assert spec.firstresult is False
    assert spec.returns_directive is False
```

- [ ] **Step 5: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/agentkit/runtime/test_hookspecs.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/agentkit/runtime/hookspecs.py tests/agentkit/runtime/test_hookspecs.py
git commit -m "feat(agentkit): add on_session_event observer hookspec"
```

---

## Task 2: ContextBuilder anchor 折叠（agentkit 层）

**Files:**
- Modify: `src/agentkit/context/builder.py:92-126`
- Modify: `tests/agentkit/context/test_builder.py`

当前 `_entry_to_message` 把所有 anchor 渲染为 `{"role": "system", "content": ...}`，无差异化。需要根据 `meta.anchor_type` 做分类渲染：

- `anchor_type == "handoff"` → 渲染为 system message，加 `[Context Summary]` 前缀
- `anchor_type == "topic_initial"` → 渲染为 system message，加 `[Topic: ...]` 前缀
- `anchor_type == "topic_finalized"` → **不渲染**（已结束的 topic 的边界标记不需要出现在 LLM context 中）
- 无 anchor_type（旧 anchor）→ 保持原有行为

- [ ] **Step 1: 写 anchor 折叠测试**

在 `tests/agentkit/context/test_builder.py` 末尾添加：

```python
def test_handoff_anchor_rendered_with_prefix(self):
    tape = Tape()
    tape.append(Entry(
        kind="anchor",
        payload={"content": "Earlier conversation about auth module"},
        meta={"anchor_type": "handoff"},
    ))
    tape.append(Entry(kind="message", payload={"role": "user", "content": "continue"}))
    builder = ContextBuilder(system_prompt="system")
    messages = builder.build(tape)
    # system + anchor + user
    assert len(messages) == 3
    assert messages[1]["role"] == "system"
    assert messages[1]["content"].startswith("[Context Summary]")
    assert "auth module" in messages[1]["content"]

def test_topic_initial_anchor_rendered_with_prefix(self):
    tape = Tape()
    tape.append(Entry(
        kind="anchor",
        payload={"content": "Fix authentication bug"},
        meta={"anchor_type": "topic_initial", "topic_id": "t-001"},
    ))
    tape.append(Entry(kind="message", payload={"role": "user", "content": "start"}))
    builder = ContextBuilder(system_prompt="system")
    messages = builder.build(tape)
    assert len(messages) == 3
    assert messages[1]["role"] == "system"
    assert messages[1]["content"].startswith("[Topic Start]")

def test_topic_finalized_anchor_skipped(self):
    tape = Tape()
    tape.append(Entry(
        kind="anchor",
        payload={"content": "Auth bug fixed successfully"},
        meta={"anchor_type": "topic_finalized", "topic_id": "t-001"},
    ))
    tape.append(Entry(kind="message", payload={"role": "user", "content": "next task"}))
    builder = ContextBuilder(system_prompt="system")
    messages = builder.build(tape)
    # system + user only (topic_finalized anchor skipped)
    assert len(messages) == 2

def test_plain_anchor_unchanged(self):
    """Anchors without meta.anchor_type behave as before."""
    tape = Tape()
    tape.append(Entry(kind="anchor", payload={"content": "Important context"}))
    tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
    builder = ContextBuilder(system_prompt="system")
    messages = builder.build(tape)
    assert len(messages) == 3
    assert messages[1]["content"] == "Important context"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/python -m pytest tests/agentkit/context/test_builder.py::TestContextBuilder::test_handoff_anchor_rendered_with_prefix tests/agentkit/context/test_builder.py::TestContextBuilder::test_topic_finalized_anchor_skipped -v`
Expected: FAIL — 当前不区分 anchor_type

- [ ] **Step 3: 修改 _entry_to_message 支持 anchor 折叠**

在 `src/agentkit/context/builder.py` 中，修改 `_entry_to_message` 方法的 anchor 分支（约 line 120-123）：

```python
elif entry.kind == "anchor":
    anchor_type = entry.meta.get("anchor_type", "")
    content = entry.payload.get("content", "")

    if anchor_type == "topic_finalized":
        return None  # Skip — finalized topic markers don't need to be in context

    if anchor_type == "handoff":
        return {
            "role": "system",
            "content": f"[Context Summary] {content}",
        }

    if anchor_type == "topic_initial":
        return {
            "role": "system",
            "content": f"[Topic Start] {content}",
        }

    # Default: plain anchor (backward compatible)
    return {
        "role": "system",
        "content": content,
    }
```

- [ ] **Step 4: 运行全部 context builder 测试**

Run: `.venv/bin/python -m pytest tests/agentkit/context/test_builder.py -v`
Expected: ALL PASS（包括原有的 `test_anchor_entries_are_preserved` 测试——plain anchor 行为不变）

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/context/builder.py tests/agentkit/context/test_builder.py
git commit -m "feat(agentkit): ContextBuilder anchor folding by anchor_type"
```

---

## Task 3: TopicPlugin — 核心实现（coding-agent 层）

**Files:**
- Create: `src/coding_agent/plugins/topic.py`
- Create: `tests/coding_agent/plugins/test_topic.py`

TopicPlugin 的职责：
1. 每次 turn 结束时（`on_checkpoint`），分析 tape 中 tool_call 的文件路径
2. 如果文件路径集合与当前 topic 的重叠率低于阈值 → 检测为新 topic
3. 写入 `topic_finalized` anchor（结束旧 topic）+ `topic_initial` anchor（开始新 topic）
4. 通过 `on_session_event` 通知其他 plugins

- [ ] **Step 1: 写基础测试——plugin 协议合规**

```python
# tests/coding_agent/plugins/test_topic.py

import pytest
from coding_agent.plugins.topic import TopicPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestTopicPlugin:
    def test_state_key(self):
        plugin = TopicPlugin()
        assert plugin.state_key == "topic"

    def test_hooks_registered(self):
        plugin = TopicPlugin()
        hooks = plugin.hooks()
        assert "on_checkpoint" in hooks
        assert "on_session_event" in hooks

    def test_mount_returns_initial_state(self):
        plugin = TopicPlugin()
        state = plugin.do_mount()
        assert "current_topic_id" in state
        assert state["current_topic_id"] is None
        assert "topic_count" in state
        assert state["topic_count"] == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/bin/python -m pytest tests/coding_agent/plugins/test_topic.py::TestTopicPlugin::test_state_key -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coding_agent.plugins.topic'`

- [ ] **Step 3: 创建 TopicPlugin 骨架**

```python
# src/coding_agent/plugins/topic.py
"""TopicPlugin — task boundary detection and topic lifecycle management.

Detects topic changes by monitoring file path overlap in tool_call entries.
When overlap drops below threshold, finalizes the old topic and starts a new one.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable

from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class TopicPlugin:
    """Plugin that manages topic lifecycle via file path heuristics."""

    state_key = "topic"

    def __init__(
        self,
        overlap_threshold: float = 0.2,
        min_entries_before_detect: int = 4,
    ) -> None:
        self._overlap_threshold = overlap_threshold
        self._min_entries = min_entries_before_detect
        self._current_topic_id: str | None = None
        self._current_topic_files: set[str] = set()
        self._topic_count: int = 0
        self._session_event_fn: Callable[..., None] | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "on_checkpoint": self.on_checkpoint,
            "on_session_event": self.on_session_event,
            "mount": self.do_mount,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "current_topic_id": self._current_topic_id,
            "topic_count": self._topic_count,
        }

    def on_session_event(
        self, event_type: str = "", payload: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        """Receive session events (observer — no return value used)."""
        pass

    def on_checkpoint(self, ctx: Any = None, **kwargs: Any) -> None:
        """Analyze tape at turn boundary for topic changes."""
        if ctx is None:
            return

        tape: Tape = ctx.tape
        entries = list(tape)

        if len(entries) < self._min_entries:
            if self._current_topic_id is None:
                self._start_topic(tape, entries)
            return

        recent_files = self._extract_files_from_recent(entries)

        if self._current_topic_id is None:
            self._start_topic(tape, entries)
            self._current_topic_files = recent_files
            return

        if not recent_files:
            return

        if not self._current_topic_files:
            self._current_topic_files = recent_files
            return

        overlap = len(recent_files & self._current_topic_files)
        total = max(len(self._current_topic_files), 1)
        overlap_ratio = overlap / total

        if overlap_ratio < self._overlap_threshold:
            self._end_topic(tape)
            self._start_topic(tape, entries)
            self._current_topic_files = recent_files
        else:
            self._current_topic_files |= recent_files

        ctx.plugin_states[self.state_key] = {
            "current_topic_id": self._current_topic_id,
            "topic_count": self._topic_count,
        }

    def _start_topic(self, tape: Tape, entries: list[Entry]) -> None:
        """Write topic_initial anchor and update state."""
        self._current_topic_id = f"topic-{uuid.uuid4().hex[:8]}"
        self._topic_count += 1

        first_user_msg = ""
        for entry in reversed(entries):
            if entry.kind == "message" and entry.payload.get("role") == "user":
                first_user_msg = entry.payload.get("content", "")[:100]
                break

        tape.append(Entry(
            kind="anchor",
            payload={"content": first_user_msg or f"Topic #{self._topic_count}"},
            meta={
                "anchor_type": "topic_initial",
                "topic_id": self._current_topic_id,
                "topic_number": self._topic_count,
            },
        ))

    def _end_topic(self, tape: Tape) -> None:
        """Write topic_finalized anchor."""
        if self._current_topic_id is None:
            return

        file_list = sorted(self._current_topic_files)[:10]
        summary = f"Topic involved files: {', '.join(file_list)}" if file_list else "Topic completed"

        tape.append(Entry(
            kind="anchor",
            payload={"content": summary},
            meta={
                "anchor_type": "topic_finalized",
                "topic_id": self._current_topic_id,
                "files": file_list,
            },
        ))

        self._current_topic_id = None
        self._current_topic_files = set()

    def _extract_files_from_recent(self, entries: list[Entry]) -> set[str]:
        """Extract file paths from recent tool_call entries."""
        files: set[str] = set()

        last_user_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].kind == "message" and entries[i].payload.get("role") == "user":
                last_user_idx = i
                break

        for entry in entries[last_user_idx:]:
            if entry.kind == "tool_call":
                args = entry.payload.get("arguments", {})
                if isinstance(args, dict):
                    for key in ("path", "file", "filename", "file_path"):
                        val = args.get(key, "")
                        if val and isinstance(val, str):
                            files.add(val)
            elif entry.kind == "message":
                content = entry.payload.get("content", "")
                paths = re.findall(r'[\w./]+\.\w{1,10}', content)
                for p in paths[:5]:
                    if '/' in p or '.' in p:
                        files.add(p)

        return files

    @property
    def current_topic_id(self) -> str | None:
        return self._current_topic_id

    @property
    def topic_count(self) -> int:
        return self._topic_count
```

- [ ] **Step 4: 运行骨架测试验证通过**

Run: `.venv/bin/python -m pytest tests/coding_agent/plugins/test_topic.py::TestTopicPlugin::test_state_key tests/coding_agent/plugins/test_topic.py::TestTopicPlugin::test_hooks_registered tests/coding_agent/plugins/test_topic.py::TestTopicPlugin::test_mount_returns_initial_state -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/topic.py tests/coding_agent/plugins/test_topic.py
git commit -m "feat(coding-agent): TopicPlugin skeleton with file path extraction"
```

---

## Task 4: TopicPlugin — 主题检测逻辑测试（coding-agent 层）

**Files:**
- Modify: `tests/coding_agent/plugins/test_topic.py`

- [ ] **Step 1: 写首次 topic 自动创建测试**

在 `tests/coding_agent/plugins/test_topic.py` 的 `TestTopicPlugin` 类中追加：

```python
def test_first_turn_creates_initial_topic(self):
    plugin = TopicPlugin()
    tape = Tape()
    tape.append(Entry(kind="message", payload={"role": "user", "content": "fix auth.py"}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/auth.py"}}))
    tape.append(Entry(kind="tool_result", payload={"tool_call_id": "tc1", "content": "file contents"}))
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "I see the issue"}))

    class FakeCtx:
        def __init__(self, tape):
            self.tape = tape
            self.plugin_states = {}

    ctx = FakeCtx(tape)
    plugin.on_checkpoint(ctx=ctx)

    assert plugin.current_topic_id is not None
    assert plugin.topic_count == 1

    # topic_initial anchor should be appended to tape
    anchors = tape.filter("anchor")
    assert len(anchors) == 1
    assert anchors[0].meta.get("anchor_type") == "topic_initial"
```

- [ ] **Step 2: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/coding_agent/plugins/test_topic.py::TestTopicPlugin::test_first_turn_creates_initial_topic -v`
Expected: PASS

- [ ] **Step 3: 写主题切换检测测试**

```python
def test_topic_switch_on_file_path_change(self):
    plugin = TopicPlugin(overlap_threshold=0.2, min_entries_before_detect=2)
    tape = Tape()

    # Turn 1: working on auth files
    tape.append(Entry(kind="message", payload={"role": "user", "content": "fix auth"}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/auth.py"}}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/auth_utils.py"}}))
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "done"}))

    class FakeCtx:
        def __init__(self, tape):
            self.tape = tape
            self.plugin_states = {}

    ctx = FakeCtx(tape)
    plugin.on_checkpoint(ctx=ctx)
    first_topic_id = plugin.current_topic_id
    assert first_topic_id is not None
    assert plugin.topic_count == 1

    # Turn 2: completely different files → should trigger topic switch
    tape.append(Entry(kind="message", payload={"role": "user", "content": "now fix the UI"}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/ui/dashboard.tsx"}}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/ui/sidebar.tsx"}}))
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "looking at UI"}))

    plugin.on_checkpoint(ctx=ctx)

    assert plugin.topic_count == 2
    assert plugin.current_topic_id != first_topic_id

    # Should have: topic_initial(1) + topic_finalized(1) + topic_initial(2) = 3 anchors
    anchors = tape.filter("anchor")
    assert len(anchors) == 3
    types = [a.meta.get("anchor_type") for a in anchors]
    assert types == ["topic_initial", "topic_finalized", "topic_initial"]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/bin/python -m pytest tests/coding_agent/plugins/test_topic.py::TestTopicPlugin::test_topic_switch_on_file_path_change -v`
Expected: PASS

- [ ] **Step 5: 写同 topic 内文件重叠测试（不触发切换）**

```python
def test_no_switch_when_files_overlap(self):
    plugin = TopicPlugin(overlap_threshold=0.2, min_entries_before_detect=2)
    tape = Tape()

    # Turn 1: working on auth
    tape.append(Entry(kind="message", payload={"role": "user", "content": "fix auth"}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/auth.py"}}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/utils.py"}}))
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "found it"}))

    class FakeCtx:
        def __init__(self, tape):
            self.tape = tape
            self.plugin_states = {}

    ctx = FakeCtx(tape)
    plugin.on_checkpoint(ctx=ctx)
    assert plugin.topic_count == 1

    # Turn 2: still auth-related (overlapping path)
    tape.append(Entry(kind="message", payload={"role": "user", "content": "now fix auth tests"}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "src/auth.py"}}))
    tape.append(Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "tests/test_auth.py"}}))
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "tests updated"}))

    plugin.on_checkpoint(ctx=ctx)

    # Should NOT switch topic — auth.py overlaps
    assert plugin.topic_count == 1
    anchors = tape.filter("anchor")
    assert len(anchors) == 1  # only the initial topic_initial
```

- [ ] **Step 6: 写 file_extract 边界情况测试**

```python
def test_extract_files_from_multiple_arg_keys(self):
    plugin = TopicPlugin()
    entries = [
        Entry(kind="tool_call", payload={"name": "file_read", "arguments": {"path": "a.py"}}),
        Entry(kind="tool_call", payload={"name": "edit_file", "arguments": {"file": "b.py"}}),
        Entry(kind="tool_call", payload={"name": "bash_run", "arguments": {"cmd": "ls"}}),
    ]
    files = plugin._extract_files_from_recent(entries)
    assert "a.py" in files
    assert "b.py" in files
    assert len(files) == 2  # bash_run has no file path

def test_no_topic_change_with_no_tool_calls(self):
    plugin = TopicPlugin(min_entries_before_detect=2)
    tape = Tape()
    tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "hi"}))

    class FakeCtx:
        def __init__(self, tape):
            self.tape = tape
            self.plugin_states = {}

    ctx = FakeCtx(tape)
    plugin.on_checkpoint(ctx=ctx)
    # Should still create initial topic even without tool calls
    assert plugin.current_topic_id is not None
    assert plugin.topic_count == 1

    tape.append(Entry(kind="message", payload={"role": "user", "content": "what is 2+2?"}))
    tape.append(Entry(kind="message", payload={"role": "assistant", "content": "4"}))
    plugin.on_checkpoint(ctx=ctx)
    # No file paths → no overlap check → no switch
    assert plugin.topic_count == 1
```

- [ ] **Step 7: 运行全部 topic 测试**

Run: `.venv/bin/python -m pytest tests/coding_agent/plugins/test_topic.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add tests/coding_agent/plugins/test_topic.py
git commit -m "test(coding-agent): TopicPlugin detection logic tests"
```

---

## Task 5: TopicPlugin 注册接线（coding-agent 层）

**Files:**
- Modify: `src/coding_agent/__main__.py:66-133`
- Modify: `src/coding_agent/agent.toml:10-22`

- [ ] **Step 1: 在 __main__.py 中注册 TopicPlugin**

在 `src/coding_agent/__main__.py` 中：

1. 在 import 块（约 line 92-100）添加：
```python
from coding_agent.plugins.topic import TopicPlugin
```

2. 在 `plugin_factories` dict（约 line 122-133 的 `plugin_factories.update(...)` 中）添加：
```python
"topic": lambda: TopicPlugin(
    overlap_threshold=float(topic_cfg.get("overlap_threshold", 0.2)),
    min_entries_before_detect=int(topic_cfg.get("min_entries", 4)),
),
```

3. 在 config 读取部分（约 line 113-115）添加：
```python
topic_cfg = cfg.extra.get("topic", {})
```

- [ ] **Step 2: 在 agent.toml 中启用 topic plugin**

在 `src/coding_agent/agent.toml` 的 `[agent.plugins] enabled` 列表中，在 `"summarizer"` 之前添加 `"topic"`：

```toml
[agent.plugins]
enabled = [
    "llm_provider",
    "storage",
    "core_tools",
    "approval",
    "doom_detector",
    "parallel_executor",
    "topic",
    "summarizer",
    "memory",
    "session_metrics",
    "shell_session",
]
```

在文件末尾添加 topic 配置 section：

```toml
[topic]
overlap_threshold = 0.2
min_entries_before_detect = 4
```

注意：`topic` 必须在 `summarizer` 之前注册，因为 topic 需要在 summarize 之前写入 anchor。

- [ ] **Step 3: 运行现有集成测试验证不破坏**

Run: `.venv/bin/python -m pytest tests/coding_agent/test_cli_pipeline.py tests/coding_agent/test_bootstrap.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/coding_agent/__main__.py src/coding_agent/agent.toml
git commit -m "feat(coding-agent): wire TopicPlugin into plugin registry"
```

---

## Task 6: 全量验证

- [ ] **Step 1: 运行全量测试**

Run: `.venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: ALL PASS（应该从 811 增加到约 825）

- [ ] **Step 2: 运行类型检查（如果配置了 mypy）**

Run: `.venv/bin/python -m mypy src/agentkit/runtime/hookspecs.py src/agentkit/context/builder.py src/coding_agent/plugins/topic.py --ignore-missing-imports 2>&1 || true`
Expected: No errors（或仅已有的 warnings）

- [ ] **Step 3: 最终 commit**

如果有遗漏的文件改动：
```bash
git add -p  # 逐个确认
git commit -m "feat: P1 complete — topic middleware layer"
```

---

## 自检清单

**1. Spec 覆盖：**
- ④ `on_session_event` hook → Task 1 ✅
- ⑤ TopicPlugin + 文件路径启发式 → Task 3 + Task 4 ✅
- ⑥ ContextBuilder anchor 折叠 → Task 2 ✅

**2. Placeholder scan：** 无 TBD / TODO / "implement later"。所有代码步骤包含完整代码。

**3. Type consistency：**
- `TopicPlugin.state_key = "topic"` — 在 `__main__.py` 和 `agent.toml` 中都引用为 `"topic"` ✅
- `Entry.meta` 的 `anchor_type` 值：`"topic_initial"` / `"topic_finalized"` — 在 `topic.py` 写入和 `builder.py` 读取时一致 ✅
- `on_session_event` 的签名 `(event_type: str, payload: dict)` — hookspec 中声明为 observer，TopicPlugin 中实现签名匹配 ✅
- `hooks()` 返回的 key 名与 hookspecs 中的 name 一致 ✅
