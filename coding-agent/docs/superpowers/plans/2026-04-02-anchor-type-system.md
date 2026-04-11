# Anchor Type System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace implicit `Entry(kind="anchor", meta={...})` conventions with a first-class `Anchor` subclass that carries `anchor_type` and `source_ids` as structured fields, enabling type-safe anchor creation, polymorphic deserialization, and entry-level provenance tracking.

**Architecture:** `Anchor` is a frozen dataclass inheriting `Entry` with `kind` pinned to `"anchor"`. `anchor_type` replaces scattered meta keys (`is_handoff`, `fold_boundary`). `source_ids` captures which entries were folded into a summary anchor. `Entry.from_dict()` gains polymorphic dispatch so all existing deserialization paths (`Tape.from_list`, `Tape.load_jsonl`) automatically produce `Anchor` instances for new-format data while remaining fully backward-compatible with old-format tapes.

**Tech Stack:** Python 3.11+, dataclasses, pytest

## Closure Re-Check Outcome — 2026-04-10

- Final verdict: `minimal-tail-then-closure`
- Closure verification command:
  `uv run pytest tests/agentkit/tape/test_anchor.py tests/agentkit/tape/test_models.py tests/agentkit/tape/test_tape.py tests/agentkit/context/test_builder.py tests/coding_agent/plugins/test_summarizer.py tests/coding_agent/plugins/test_topic.py -v`
- Result: `105 passed`
- Task 3 status during sequential closure re-check: executed to remove the import-cycle tail
- Proven tape-view gate invariants:
  1. typed anchor dispatch
  2. anchor-aware tape/window behavior
  3. consumer-visible anchor handling

Anchor therefore closes after one minimal semantic tail fix, and the tape-view gate may open on the strength of these proven invariants.

---

### Task 1: Anchor class definition and serialization

**Files:**
- Create: `src/agentkit/tape/anchor.py`
- Test: `tests/agentkit/tape/test_anchor.py`

- [ ] **Step 1: Write the failing tests for Anchor**

```python
# tests/agentkit/tape/test_anchor.py
import pytest
from agentkit.tape.anchor import Anchor, AnchorType
from agentkit.tape.models import Entry


class TestAnchor:
    def test_anchor_is_entry_subclass(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
        )
        assert isinstance(anchor, Entry)

    def test_kind_is_always_anchor(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
        )
        assert anchor.kind == "anchor"

    def test_anchor_is_frozen(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
        )
        with pytest.raises(AttributeError):
            anchor.anchor_type = "fold"

    def test_is_handoff_property(self):
        assert Anchor(anchor_type="handoff", payload={}).is_handoff is True
        assert Anchor(anchor_type="topic_start", payload={}).is_handoff is False

    def test_fold_boundary_property(self):
        assert Anchor(anchor_type="fold", payload={}).fold_boundary is True
        assert Anchor(anchor_type="topic_end", payload={}).fold_boundary is True
        assert Anchor(anchor_type="handoff", payload={}).fold_boundary is False
        assert Anchor(anchor_type="topic_start", payload={}).fold_boundary is False

    def test_source_ids_default_empty(self):
        anchor = Anchor(anchor_type="handoff", payload={})
        assert anchor.source_ids == ()

    def test_source_ids_stored(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={},
            source_ids=("id-1", "id-2"),
        )
        assert anchor.source_ids == ("id-1", "id-2")

    def test_to_dict_includes_anchor_fields(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "summary"},
            source_ids=("a", "b"),
            meta={"prefix": "Context Summary"},
        )
        d = anchor.to_dict()
        assert d["kind"] == "anchor"
        assert d["anchor_type"] == "handoff"
        assert d["source_ids"] == ["a", "b"]
        assert d["meta"]["prefix"] == "Context Summary"
        assert "id" in d
        assert "timestamp" in d

    def test_to_dict_omits_empty_source_ids(self):
        anchor = Anchor(anchor_type="fold", payload={})
        d = anchor.to_dict()
        assert "source_ids" not in d

    def test_from_dict_roundtrip(self):
        original = Anchor(
            anchor_type="topic_end",
            payload={"content": "topic done"},
            source_ids=("x", "y", "z"),
            meta={"topic_id": "t1"},
        )
        restored = Anchor.from_dict(original.to_dict())
        assert isinstance(restored, Anchor)
        assert restored.anchor_type == "topic_end"
        assert restored.source_ids == ("x", "y", "z")
        assert restored.meta["topic_id"] == "t1"
        assert restored.id == original.id

    def test_from_dict_without_source_ids(self):
        d = {
            "id": "a1",
            "kind": "anchor",
            "payload": {"content": "summary"},
            "timestamp": 1000.0,
            "anchor_type": "handoff",
        }
        anchor = Anchor.from_dict(d)
        assert anchor.source_ids == ()
        assert anchor.anchor_type == "handoff"

    def test_meta_preserved(self):
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": "s"},
            meta={"folded_topics": ["t1", "t2"], "prefix": "Context Summary"},
        )
        assert anchor.meta["folded_topics"] == ["t1", "t2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/tape/test_anchor.py -v`
Expected: ImportError — `agentkit.tape.anchor` does not exist

- [ ] **Step 3: Implement Anchor class**

```python
# src/agentkit/tape/anchor.py
"""Anchor — structured checkpoint entry for tape windowing and provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agentkit.tape.models import Entry

AnchorType = Literal["handoff", "topic_start", "topic_end", "fold"]


@dataclass(frozen=True)
class Anchor(Entry):
    """Typed anchor entry with structured fields replacing implicit meta conventions.

    anchor_type: semantic role of this anchor
    source_ids: IDs of entries folded into this anchor (provenance)
    """

    anchor_type: AnchorType = "handoff"
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", "anchor")

    @property
    def is_handoff(self) -> bool:
        return self.anchor_type == "handoff"

    @property
    def fold_boundary(self) -> bool:
        return self.anchor_type in ("fold", "topic_end")

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["anchor_type"] = self.anchor_type
        if self.source_ids:
            d["source_ids"] = list(self.source_ids)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Anchor:
        return cls(
            id=data["id"],
            kind="anchor",
            payload=data["payload"],
            timestamp=data["timestamp"],
            meta=data.get("meta", {}),
            anchor_type=data.get("anchor_type", "handoff"),
            source_ids=tuple(data.get("source_ids", ())),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/tape/test_anchor.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/tape/anchor.py tests/agentkit/tape/test_anchor.py
git commit -m "feat(agentkit): add Anchor type with anchor_type and source_ids"
```

---

### Task 2: Polymorphic Entry.from_dict dispatch

**Files:**
- Modify: `src/agentkit/tape/models.py:30-38`
- Test: `tests/agentkit/tape/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/agentkit/tape/test_models.py

    def test_from_dict_returns_anchor_for_new_format(self):
        from agentkit.tape.anchor import Anchor

        d = {
            "id": "a1",
            "kind": "anchor",
            "payload": {"content": "summary"},
            "timestamp": 1000.0,
            "anchor_type": "handoff",
            "source_ids": ["id1", "id2"],
        }
        entry = Entry.from_dict(d)
        assert isinstance(entry, Anchor)
        assert entry.anchor_type == "handoff"
        assert entry.source_ids == ("id1", "id2")

    def test_from_dict_returns_plain_entry_for_old_anchor_format(self):
        d = {
            "id": "a2",
            "kind": "anchor",
            "payload": {"content": "old summary"},
            "timestamp": 1000.0,
            "meta": {"is_handoff": True},
        }
        entry = Entry.from_dict(d)
        assert type(entry) is Entry  # NOT Anchor
        assert entry.kind == "anchor"
        assert entry.meta["is_handoff"] is True

    def test_from_dict_returns_plain_entry_for_non_anchor(self):
        d = {
            "id": "m1",
            "kind": "message",
            "payload": {"role": "user", "content": "hi"},
            "timestamp": 1000.0,
        }
        entry = Entry.from_dict(d)
        assert type(entry) is Entry
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/tape/test_models.py::TestEntry::test_from_dict_returns_anchor_for_new_format -v`
Expected: FAIL — `Entry.from_dict` returns `Entry`, not `Anchor`

- [ ] **Step 3: Modify Entry.from_dict for polymorphic dispatch**

Replace `Entry.from_dict` in `src/agentkit/tape/models.py:30-38`:

```python
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        if data.get("kind") == "anchor" and "anchor_type" in data:
            from agentkit.tape.anchor import Anchor

            return Anchor.from_dict(data)
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=data["payload"],
            timestamp=data["timestamp"],
            meta=data.get("meta", {}),
        )
```

- [ ] **Step 4: Run all model tests to verify they pass**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/tape/test_models.py -v`
Expected: All tests PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/tape/models.py tests/agentkit/tape/test_models.py
git commit -m "feat(agentkit): polymorphic Entry.from_dict dispatches to Anchor"
```

---

### Task 3: Update Tape.load_jsonl to use Anchor properties

**Files:**
- Modify: `src/agentkit/tape/tape.py:93-108`
- Test: `tests/agentkit/tape/test_tape.py`

- [ ] **Step 1: Write the failing test for new-format JSONL loading**

```python
# Append to tests/agentkit/tape/test_tape.py

    def test_load_jsonl_new_anchor_format(self, tmp_path):
        """New-format Anchor with anchor_type field sets window_start correctly."""
        import json as _json
        from agentkit.tape.anchor import Anchor

        path = tmp_path / "new_anchor.jsonl"
        entries = [
            {
                "id": "m1",
                "kind": "message",
                "payload": {"role": "user", "content": "old"},
                "timestamp": 0,
            },
            {
                "id": "a1",
                "kind": "anchor",
                "payload": {"content": "summary"},
                "timestamp": 0,
                "anchor_type": "handoff",
                "source_ids": ["m1"],
            },
            {
                "id": "m2",
                "kind": "message",
                "payload": {"role": "user", "content": "new"},
                "timestamp": 0,
            },
        ]
        path.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")
        tape = Tape.load_jsonl(path)

        assert tape.window_start == 1
        assert isinstance(tape[1], Anchor)
        assert tape[1].is_handoff is True
        assert tape[1].source_ids == ("m1",)
        windowed = tape.windowed_entries()
        assert len(windowed) == 2  # anchor + "new"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/tape/test_tape.py::TestTape::test_load_jsonl_new_anchor_format -v`
Expected: FAIL — either `isinstance` check fails or `window_start` is wrong (current code only checks `meta.get("is_handoff")`)

- [ ] **Step 3: Update Tape.load_jsonl**

Replace lines 93-108 in `src/agentkit/tape/tape.py`:

```python
    @classmethod
    def load_jsonl(cls, path: Path, **kwargs: Any) -> Tape:
        from agentkit.tape.anchor import Anchor

        entries: list[Entry] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(Entry.from_dict(json.loads(line)))
        window_start = 0
        for i, entry in enumerate(entries):
            if isinstance(entry, Anchor):
                if entry.is_handoff:
                    window_start = i
            elif entry.kind == "anchor":
                # Old-format backward compatibility
                anchor_type = entry.meta.get("anchor_type")
                if anchor_type == "handoff" and "is_handoff" not in entry.meta:
                    entry.meta["is_handoff"] = True
                if anchor_type == "topic_finalized" and "fold_boundary" not in entry.meta:
                    entry.meta["fold_boundary"] = True
                if entry.meta.get("is_handoff"):
                    window_start = i
        return cls(
            entries=entries,
            _window_start=window_start,
            _persisted_count=len(entries),
            **kwargs,
        )
```

- [ ] **Step 4: Run all tape tests**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/tape/test_tape.py -v`
Expected: All tests PASS (existing backward-compat tests + new test)

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/tape/tape.py tests/agentkit/tape/test_tape.py
git commit -m "feat(agentkit): Tape.load_jsonl uses Anchor.is_handoff with old-format fallback"
```

---

### Task 4: Update ContextBuilder to handle Anchor type

**Files:**
- Modify: `src/agentkit/context/builder.py:141-150`
- Test: `tests/agentkit/context/test_builder.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/agentkit/context/test_builder.py

    def test_anchor_fold_boundary_skipped(self):
        """Anchor with fold_boundary (topic_end/fold) should not be rendered."""
        from agentkit.tape.anchor import Anchor

        tape = Tape()
        tape.append(Anchor(
            anchor_type="topic_end",
            payload={"content": "topic done"},
            meta={"topic_id": "t1"},
        ))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "next"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 2  # system + user only (anchor skipped)

    def test_anchor_handoff_rendered_as_system(self):
        """Anchor with anchor_type=handoff should render as system message."""
        from agentkit.tape.anchor import Anchor

        tape = Tape()
        tape.append(Anchor(
            anchor_type="handoff",
            payload={"content": "Earlier context summary"},
            meta={"prefix": "Context Summary"},
        ))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3  # system + anchor-as-system + user
        assert messages[1]["role"] == "system"
        assert messages[1]["content"].startswith("[Context Summary]")

    def test_anchor_topic_start_rendered(self):
        """Anchor with anchor_type=topic_start should render (not fold_boundary)."""
        from agentkit.tape.anchor import Anchor

        tape = Tape()
        tape.append(Anchor(
            anchor_type="topic_start",
            payload={"content": "New topic about auth"},
            meta={"prefix": "Topic Start"},
        ))
        tape.append(Entry(kind="message", payload={"role": "user", "content": "go"}))
        builder = ContextBuilder(system_prompt="system")
        messages = builder.build(tape)
        assert len(messages) == 3  # system + anchor + user
        assert "[Topic Start]" in messages[1]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/context/test_builder.py::TestContextBuilder::test_anchor_fold_boundary_skipped -v`
Expected: FAIL — fold_boundary Anchor currently rendered (no `isinstance` check)

- [ ] **Step 3: Update ContextBuilder._entry_to_message**

Replace lines 141-150 in `src/agentkit/context/builder.py`:

```python
        elif entry.kind == "anchor":
            from agentkit.tape.anchor import Anchor

            if isinstance(entry, Anchor) and entry.fold_boundary:
                return None
            if entry.meta.get("skip"):
                return None

            content = entry.payload.get("content", "")
            prefix = entry.meta.get("prefix")
            if prefix:
                content = f"[{prefix}] {content}"

            return {"role": "system", "content": content}
```

- [ ] **Step 4: Run all builder tests**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/agentkit/context/test_builder.py -v`
Expected: All tests PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/agentkit/context/builder.py tests/agentkit/context/test_builder.py
git commit -m "feat(agentkit): ContextBuilder skips fold_boundary Anchors, renders others"
```

---

### Task 5: Update tape/__init__.py exports

**Files:**
- Modify: `src/agentkit/tape/__init__.py`

- [ ] **Step 1: Update exports**

```python
# src/agentkit/tape/__init__.py
from agentkit.tape.anchor import Anchor, AnchorType
from agentkit.tape.models import Entry
from agentkit.tape.store import ForkTapeStore
from agentkit.tape.tape import Tape

__all__ = ["Anchor", "AnchorType", "Entry", "ForkTapeStore", "Tape"]
```

- [ ] **Step 2: Verify import works**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run python -c "from agentkit.tape import Anchor, AnchorType; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/agentkit/tape/__init__.py
git commit -m "feat(agentkit): export Anchor and AnchorType from tape package"
```

---

### Task 6: Migrate SummarizerPlugin to Anchor

**Files:**
- Modify: `src/coding_agent/plugins/summarizer.py:75-101,103-128,131-168`
- Modify: `tests/coding_agent/plugins/test_summarizer.py`

- [ ] **Step 1: Update test helpers and add Anchor-specific assertions**

```python
# tests/coding_agent/plugins/test_summarizer.py
import pytest
from coding_agent.plugins.summarizer import SummarizerPlugin
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry
from agentkit.tape.anchor import Anchor


def _make_topic_initial(topic_id: str, topic_number: int = 1) -> Anchor:
    return Anchor(
        anchor_type="topic_start",
        payload={"content": f"Topic #{topic_number}"},
        meta={
            "topic_id": topic_id,
            "topic_number": topic_number,
            "prefix": "Topic Start",
        },
    )


def _make_topic_finalized(topic_id: str, files: list[str] | None = None) -> Anchor:
    return Anchor(
        anchor_type="topic_end",
        payload={"content": f"Topic involved files: {', '.join(files or [])}"},
        meta={
            "topic_id": topic_id,
            "files": files or [],
        },
    )
```

Update existing assertions in `TestSummarizerPlugin.test_long_tape_gets_summarized`:

```python
    def test_long_tape_gets_summarized(self):
        plugin = SummarizerPlugin(max_entries=5)
        tape = Tape()
        for i in range(20):
            tape.append(
                Entry(
                    kind="message",
                    payload={"role": "user", "content": f"message number {i}"},
                )
            )
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        split_point, anchor = result
        assert isinstance(split_point, int)
        assert isinstance(anchor, Anchor)
        assert anchor.is_handoff is True
        assert len(anchor.source_ids) == 2  # first and last entry IDs
```

Add new test for source_ids provenance:

```python
    def test_summary_anchor_has_source_ids(self):
        """Handoff anchors carry source_ids for provenance tracking."""
        plugin = SummarizerPlugin(max_entries=5, keep_recent=3)
        tape = Tape()
        entries = []
        for i in range(20):
            e = Entry(
                kind="message",
                payload={"role": "user", "content": f"msg-{i}"},
            )
            entries.append(e)
            tape.append(e)
        result = plugin.resolve_context_window(tape=tape)
        assert result is not None
        _, anchor = result
        assert isinstance(anchor, Anchor)
        assert anchor.source_ids == (entries[0].id, entries[16].id)
```

Update `test_find_last_finalized_uses_fold_boundary` to use Anchor:

```python
    def test_find_last_finalized_uses_fold_boundary(self):
        plugin = SummarizerPlugin(max_entries=5)
        entries = [
            Entry(kind="message", payload={"role": "user", "content": "a"}),
            Anchor(
                anchor_type="topic_end",
                payload={"content": "fold"},
            ),
            Entry(kind="message", payload={"role": "user", "content": "b"}),
        ]
        idx = plugin._find_last_finalized(entries)
        assert idx == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_summarizer.py -v`
Expected: FAIL — `_build_topic_summary` / `_build_entry_summary` still return `Entry`, not `Anchor`

- [ ] **Step 3: Migrate SummarizerPlugin to use Anchor**

Update `src/coding_agent/plugins/summarizer.py`:

Add import at top:
```python
from agentkit.tape.anchor import Anchor
```

Replace `_find_last_finalized` (lines 69-73):
```python
    def _find_last_finalized(self, entries: list[Entry]) -> int | None:
        for i in range(len(entries) - 1, -1, -1):
            entry = entries[i]
            if isinstance(entry, Anchor) and entry.fold_boundary:
                return i
            if entry.kind == "anchor" and entry.meta.get("fold_boundary"):
                return i  # old-format backward compat
        return None
```

Replace `_build_topic_summary` (lines 75-101):
```python
    def _build_topic_summary(self, old_entries: list[Entry]) -> Anchor:
        topic_ids = []
        for e in old_entries:
            tid = e.meta.get("topic_id")
            if tid and tid not in topic_ids:
                topic_ids.append(tid)

        files: list[str] = []
        for e in old_entries:
            if (isinstance(e, Anchor) and e.fold_boundary) or e.meta.get(
                "fold_boundary"
            ):
                files.extend(e.meta.get("files", []))

        topic_count = len(topic_ids) or 1
        summary_text = f"[Summarized {len(old_entries)} entries from {topic_count} completed topic(s)]"
        if files:
            summary_text += f"\nFiles involved: {', '.join(sorted(set(files))[:10])}"

        return Anchor(
            anchor_type="handoff",
            source_ids=(old_entries[0].id, old_entries[-1].id),
            payload={"content": summary_text},
            meta={
                "folded_topics": topic_ids,
                "prefix": "Context Summary",
            },
        )
```

Replace `_build_entry_summary` (lines 103-129):
```python
    def _build_entry_summary(self, old_entries: list[Entry]) -> Anchor:
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

        summary_text = f"[Summarized {len(old_entries)} earlier entries]\n" + "\n".join(
            summary_parts[-10:]
        )

        return Anchor(
            anchor_type="handoff",
            source_ids=(old_entries[0].id, old_entries[-1].id),
            payload={"content": summary_text},
            meta={
                "prefix": "Context Summary",
            },
        )
```

Replace `summarize_context` anchor creation (lines 163-166):
```python
        anchor = Anchor(
            anchor_type="handoff",
            payload={"content": summary_text},
        )
```

- [ ] **Step 4: Run all summarizer tests**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_summarizer.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/summarizer.py tests/coding_agent/plugins/test_summarizer.py
git commit -m "refactor(coding-agent): SummarizerPlugin emits Anchor with source_ids"
```

---

### Task 7: Migrate TopicPlugin to Anchor

**Files:**
- Modify: `src/coding_agent/plugins/topic.py:107-117,141-152`
- Modify: `tests/coding_agent/plugins/test_topic.py`

- [ ] **Step 1: Update test assertions**

Add import at top of `tests/coding_agent/plugins/test_topic.py`:
```python
from agentkit.tape.anchor import Anchor
```

Update `test_first_turn_creates_initial_topic` assertion:
```python
        anchors = tape.filter("anchor")
        assert len(anchors) == 1
        assert isinstance(anchors[0], Anchor)
        assert anchors[0].anchor_type == "topic_start"
        assert anchors[0].meta.get("prefix") == "Topic Start"
```

Update `test_topic_switch_on_file_path_change` fold_boundary assertion:
```python
        anchors = tape.filter("anchor")
        assert len(anchors) == 3
        assert anchors[0].anchor_type == "topic_start"
        assert isinstance(anchors[1], Anchor)
        assert anchors[1].fold_boundary is True
        assert anchors[1].anchor_type == "topic_end"
        assert anchors[2].anchor_type == "topic_start"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_topic.py::TestTopicPlugin::test_first_turn_creates_initial_topic -v`
Expected: FAIL — `isinstance(anchors[0], Anchor)` fails because `TopicPlugin` still creates `Entry`

- [ ] **Step 3: Migrate TopicPlugin to use Anchor**

Update `src/coding_agent/plugins/topic.py`:

Add import at top:
```python
from agentkit.tape.anchor import Anchor
```

Replace `_start_topic` anchor creation (lines 107-117):
```python
        tape.append(
            Anchor(
                anchor_type="topic_start",
                payload={"content": first_user_msg or f"Topic #{self._topic_count}"},
                meta={
                    "topic_id": self._current_topic_id,
                    "topic_number": self._topic_count,
                    "prefix": "Topic Start",
                },
            )
        )
```

Replace `_end_topic` anchor creation (lines 141-152):
```python
        tape.append(
            Anchor(
                anchor_type="topic_end",
                payload={"content": summary},
                meta={
                    "topic_id": self._current_topic_id,
                    "files": file_list,
                },
            )
        )
```

- [ ] **Step 4: Run all topic tests**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_topic.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/topic.py tests/coding_agent/plugins/test_topic.py
git commit -m "refactor(coding-agent): TopicPlugin emits Anchor(topic_start/topic_end)"
```

---

### Task 8: Update test_memory.py helpers and cross-cutting test pass

**Files:**
- Modify: `tests/coding_agent/plugins/test_memory.py:81-90`

- [ ] **Step 1: Update test_memory.py helper to use Anchor**

Replace `_make_topic_initial` in `tests/coding_agent/plugins/test_memory.py`:
```python
from agentkit.tape.anchor import Anchor


def _make_topic_initial(topic_id: str, topic_number: int = 1) -> Anchor:
    return Anchor(
        anchor_type="topic_start",
        payload={"content": f"Topic #{topic_number}"},
        meta={
            "topic_id": topic_id,
            "topic_number": topic_number,
            "prefix": "Topic Start",
        },
    )
```

- [ ] **Step 2: Run memory tests**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/coding_agent/plugins/test_memory.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent && uv run pytest tests/ -v --tb=short`
Expected: All tests PASS — no regressions

- [ ] **Step 4: Commit**

```bash
git add tests/coding_agent/plugins/test_memory.py
git commit -m "test: update memory test helpers to use Anchor type"
```

## Closure Note — 2026-04-10

- Anchor closure re-check outcome: Task 2 showed the bucket was behaviorally closure-ready, but Task 4 verification exposed one real static-quality tail.
- Task 3 executed to remove the import cycle between `src/agentkit/tape/anchor.py` and `src/agentkit/tape/models.py` while preserving `Entry.from_dict()` polymorphic anchor behavior.
- Fresh anchor closure verification passed:
  - `uv run pytest tests/agentkit/tape/test_anchor.py tests/agentkit/tape/test_models.py tests/agentkit/tape/test_tape.py tests/agentkit/context/test_builder.py tests/coding_agent/plugins/test_summarizer.py tests/coding_agent/plugins/test_topic.py -v` → `105 passed`
- Diagnostics on touched anchor files are clean:
  - `src/agentkit/tape/anchor.py`
  - `src/agentkit/tape/models.py`
  - `src/agentkit/tape/tape.py`
  - `src/agentkit/context/builder.py`
  - `src/coding_agent/plugins/summarizer.py`
  - `src/coding_agent/plugins/topic.py`
- Final verdict: `minimal-tail-then-closure`
- Tape-view gate invariants proven:
  1. typed anchor dispatch
  2. anchor-aware tape/window behavior
  3. consumer-visible anchor handling
- Closure evidence: `.sisyphus/evidence/anchor-type-system-closure-2026-04-10.txt`
