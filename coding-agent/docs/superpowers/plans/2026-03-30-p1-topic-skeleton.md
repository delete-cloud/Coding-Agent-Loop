# P1 Topic Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mid-granularity "topic" abstraction to the Tape-based agent system — between a turn and a session — using zero-LLM-cost file-path overlap heuristics and a new observer hook.

**Architecture:** Three coordinated changes: (1) a new `on_session_event` observer hook in agentkit lets the framework broadcast session-level transitions without knowing what a "topic" is; (2) `TopicPlugin` in coding-agent detects topic boundaries by watching tool_call file paths across turns and writes `topic_initial`/`topic_finalized` anchor entries to the tape; (3) `ContextBuilder` in agentkit learns to differentiate anchor types via `entry.meta["anchor_type"]` so that topic separators and handoff summaries render differently in the LLM prompt.

**Tech Stack:** Python 3.12, pytest, agentkit plugin protocol (state_key + hooks()), Entry(kind="anchor", meta={"anchor_type": ...}), Tape.append(), HookRuntime.notify()

---

## File Map

### Files Modified

| File | Change |
|---|---|
| `src/agentkit/runtime/hookspecs.py` | Add `on_session_event` HookSpec (observer) |
| `src/agentkit/runtime/pipeline.py` | Call `notify("on_session_event", ...)` at turn end |
| `src/agentkit/context/builder.py` | Differentiate anchor rendering by `meta["anchor_type"]` |
| `tests/agentkit/runtime/test_hookspecs.py` | Update hardcoded "12 hooks" count to 13 |

### Files Created

| File | Purpose |
|---|---|
| `src/coding_agent/plugins/topic.py` | TopicPlugin — file-path overlap topic detection |
| `tests/coding_agent/plugins/test_topic.py` | Unit tests for TopicPlugin |
| `tests/agentkit/context/test_builder_anchor_folding.py` | Tests for anchor-type-aware ContextBuilder rendering |

---

## Task 1: Add `on_session_event` hook to agentkit

**Files:**
- Modify: `src/agentkit/runtime/hookspecs.py`
- Modify: `tests/agentkit/runtime/test_hookspecs.py`

- [ ] **Step 1: Update the failing test first (TDD)**

  Open `tests/agentkit/runtime/test_hookspecs.py` and update the hardcoded count test:

  ```python
  # OLD — line 8 in test_all_12_hooks_defined:
  def test_all_12_hooks_defined(self):
      expected = {
          "provide_storage", "get_tools", "provide_llm", "approve_tool_call",
          "summarize_context", "resolve_context_window", "on_error", "mount",
          "on_checkpoint", "build_context", "on_turn_end", "execute_tool",
      }
      assert set(HOOK_SPECS.keys()) == expected

  # NEW — rename the test and add on_session_event:
  def test_all_13_hooks_defined(self):
      expected = {
          "provide_storage", "get_tools", "provide_llm", "approve_tool_call",
          "summarize_context", "resolve_context_window", "on_error", "mount",
          "on_checkpoint", "build_context", "on_turn_end", "execute_tool",
          "on_session_event",
      }
      assert set(HOOK_SPECS.keys()) == expected
  ```

  Also add a new test at the end of `TestHookSpecs`:

  ```python
  def test_on_session_event_is_observer(self):
      spec = HOOK_SPECS["on_session_event"]
      assert spec.is_observer is True
      assert spec.firstresult is False
      assert spec.returns_directive is False
  ```

- [ ] **Step 2: Run the test to verify it fails**

  ```bash
  python3 -m pytest tests/agentkit/runtime/test_hookspecs.py -v
  ```

  Expected: `FAILED test_all_13_hooks_defined` — `on_session_event` not in HOOK_SPECS yet.

- [ ] **Step 3: Add the HookSpec**

  In `src/agentkit/runtime/hookspecs.py`, add to the `HOOK_SPECS` dict (after `on_checkpoint`):

  ```python
  "on_session_event": HookSpec(
      name="on_session_event",
      is_observer=True,
      doc=(
          "Observer: notified on session-level events (e.g. topic transitions, "
          "handoffs). Receives event_type: str and payload: dict. Cannot affect flow."
      ),
  ),
  ```

- [ ] **Step 4: Run the hookspecs tests to verify they pass**

  ```bash
  python3 -m pytest tests/agentkit/runtime/test_hookspecs.py -v
  ```

  Expected: All tests PASS (including renamed `test_all_13_hooks_defined` and new `test_on_session_event_is_observer`).

- [ ] **Step 5: Commit**

  ```bash
  git add src/agentkit/runtime/hookspecs.py tests/agentkit/runtime/test_hookspecs.py
  git commit -m "feat(agentkit): add on_session_event observer hook"
  ```

---

## Task 2: Fire `on_session_event` from Pipeline at turn end

**Files:**
- Modify: `src/agentkit/runtime/pipeline.py`
- Modify: `tests/agentkit/runtime/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

  In `tests/agentkit/runtime/test_pipeline.py`, add a test class that verifies `on_session_event` is notified at turn end. Add after the existing `TestPipelineStages` class:

  ```python
  class TestOnSessionEventNotify:
      """Pipeline fires on_session_event at end of each turn."""

      def _make_pipeline(self, plugin):
          registry = PluginRegistry()
          registry.register(plugin)
          runtime = HookRuntime(registry)
          return Pipeline(runtime, registry)

      @pytest.mark.asyncio
      async def test_on_session_event_fired_on_turn_end(self):
          events_received = []

          class EventCapture:
              state_key = "event_capture"

              def hooks(self):
                  return {
                      "on_session_event": self.on_session_event,
                      "provide_llm": self.provide_llm,
                      "provide_storage": self.provide_storage,
                      "get_tools": self.get_tools,
                      "build_context": self.build_context,
                      "execute_tool": self.execute_tool,
                  }

              def on_session_event(self, event_type=None, payload=None, **kwargs):
                  events_received.append({"event_type": event_type, "payload": payload})

              def provide_llm(self, **kwargs):
                  from unittest.mock import AsyncMock, MagicMock
                  from agentkit.providers.models import TextEvent, DoneEvent
                  mock_llm = MagicMock()
                  async def stream(messages, tools=None):
                      yield TextEvent(text="hello")
                      yield DoneEvent()
                  mock_llm.stream = stream
                  return mock_llm

              def provide_storage(self, **kwargs):
                  return None

              def get_tools(self, **kwargs):
                  return []

              def build_context(self, **kwargs):
                  return []

              def execute_tool(self, name="", **kwargs):
                  return ""

          from agentkit.tape.tape import Tape
          from agentkit.tape.models import Entry
          plugin = EventCapture()
          pipeline = self._make_pipeline(plugin)
          tape = Tape()
          tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
          ctx = PipelineContext(tape=tape, session_id="test-ses")
          await pipeline.run_turn(ctx)

          assert len(events_received) == 1
          assert events_received[0]["event_type"] == "turn_end"
          assert "session_id" in events_received[0]["payload"]
  ```

- [ ] **Step 2: Run to verify it fails**

  ```bash
  python3 -m pytest tests/agentkit/runtime/test_pipeline.py::TestOnSessionEventNotify -v
  ```

  Expected: `FAILED` — Pipeline does not yet fire `on_session_event`.

- [ ] **Step 3: Add the notify call to Pipeline._stage_save_state**

  In `src/agentkit/runtime/pipeline.py`, update `_stage_save_state`:

  ```python
  async def _stage_save_state(self, ctx: PipelineContext) -> None:
      self._runtime.notify("on_checkpoint", ctx=ctx)
      self._runtime.notify(
          "on_session_event",
          event_type="turn_end",
          payload={"session_id": ctx.session_id, "tape_len": len(ctx.tape)},
      )
  ```

- [ ] **Step 4: Run the new test to verify it passes**

  ```bash
  python3 -m pytest tests/agentkit/runtime/test_pipeline.py::TestOnSessionEventNotify -v
  ```

  Expected: PASS.

- [ ] **Step 5: Run full pipeline test suite to confirm no regression**

  ```bash
  python3 -m pytest tests/agentkit/runtime/test_pipeline.py -v
  ```

  Expected: All PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add src/agentkit/runtime/pipeline.py tests/agentkit/runtime/test_pipeline.py
  git commit -m "feat(agentkit): fire on_session_event at turn end from pipeline"
  ```

---

## Task 3: TopicPlugin — file-path overlap topic detection

**Files:**
- Create: `src/coding_agent/plugins/topic.py`
- Create: `tests/coding_agent/plugins/test_topic.py`

### Design

TopicPlugin listens on `on_session_event` (observer). On each `turn_end` event, it reads the **current turn's tool_call entries** (entries appended since the last turn boundary) from the tape, extracts file paths from their arguments, and compares with the **previous turn's file paths**.

**Topic boundary heuristic:** if the current turn's file set and the previous turn's file set are **non-empty AND disjoint** (zero overlap), a new topic has begun.

**Anchor writing:** TopicPlugin appends anchor entries directly to the tape:
- `topic_initial` — written when a new topic boundary is detected (at the *end* of the new topic's first turn)
- `topic_finalized` — written when the current topic closes (when a new disjoint topic starts, the *previous* topic is finalized first)

Both anchors carry:
- `meta["anchor_type"]`: `"topic_initial"` or `"topic_finalized"`
- `meta["topic_id"]`: a UUID stable across initial/finalized pair
- `meta["file_paths"]`: list of file paths associated with this topic

TopicPlugin receives the `tape` via the `on_session_event` payload (Pipeline passes `ctx` in `on_checkpoint`). However, `on_session_event` only receives `event_type` and `payload` — the tape itself must be passed in the payload. We update the Pipeline's notify call to include the tape.

**Note:** TopicPlugin does NOT use any LLM. It only reads `entry.kind == "tool_call"` and `entry.payload["arguments"]` (which is a dict or JSON string).

- [ ] **Step 1: Update Pipeline to include tape in on_session_event payload**

  In `src/agentkit/runtime/pipeline.py`, update `_stage_save_state`:

  ```python
  async def _stage_save_state(self, ctx: PipelineContext) -> None:
      self._runtime.notify("on_checkpoint", ctx=ctx)
      self._runtime.notify(
          "on_session_event",
          event_type="turn_end",
          payload={
              "session_id": ctx.session_id,
              "tape_len": len(ctx.tape),
              "tape": ctx.tape,
          },
      )
  ```

- [ ] **Step 2: Write the TopicPlugin tests**

  Create `tests/coding_agent/plugins/test_topic.py`:

  ```python
  import re
  import pytest
  from agentkit.tape.tape import Tape
  from agentkit.tape.models import Entry
  from coding_agent.plugins.topic import TopicPlugin


  def _make_tool_call(name: str, path: str) -> Entry:
      return Entry(
          kind="tool_call",
          payload={"name": name, "arguments": {"path": path}, "role": "assistant"},
      )


  def _make_message(role: str, content: str) -> Entry:
      return Entry(kind="message", payload={"role": role, "content": content})


  def _fire_turn_end(plugin: TopicPlugin, tape: Tape) -> None:
      plugin.on_session_event(
          event_type="turn_end",
          payload={"session_id": "ses-1", "tape_len": len(tape), "tape": tape},
      )


  class TestTopicPluginBasics:
      def test_state_key(self):
          assert TopicPlugin.state_key == "topic"

      def test_hooks_registers_on_session_event(self):
          plugin = TopicPlugin()
          assert "on_session_event" in plugin.hooks()

      def test_ignores_non_turn_end_events(self):
          plugin = TopicPlugin()
          tape = Tape()
          # Should not raise and should not write any anchors
          plugin.on_session_event(
              event_type="some_other_event",
              payload={"tape": tape},
          )
          assert len(tape) == 0


  class TestTopicBoundaryDetection:
      def test_first_turn_no_anchor_written(self):
          """First turn always starts a topic but writes no anchor until next turn."""
          plugin = TopicPlugin()
          tape = Tape()
          tape.append(_make_message("user", "fix auth.py"))
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          tape.append(_make_message("assistant", "done"))
          _fire_turn_end(plugin, tape)
          # No anchor yet — need a second turn to compare with
          anchors = [e for e in tape if e.kind == "anchor"]
          assert len(anchors) == 0

      def test_same_files_no_new_topic(self):
          """Overlapping file sets → same topic, no boundary."""
          plugin = TopicPlugin()
          tape = Tape()
          # Turn 1
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          _fire_turn_end(plugin, tape)
          # Turn 2 — same file
          tape.append(_make_tool_call("file_write", "src/auth.py"))
          _fire_turn_end(plugin, tape)
          anchors = [e for e in tape if e.kind == "anchor"]
          assert len(anchors) == 0

      def test_disjoint_files_triggers_topic_boundary(self):
          """Disjoint file sets → new topic. Two anchors written: topic_finalized + topic_initial."""
          plugin = TopicPlugin()
          tape = Tape()
          # Turn 1: working on auth
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          _fire_turn_end(plugin, tape)
          # Turn 2: completely different file
          tape.append(_make_tool_call("file_read", "src/billing.py"))
          _fire_turn_end(plugin, tape)
          anchors = [e for e in tape if e.kind == "anchor"]
          anchor_types = [a.meta["anchor_type"] for a in anchors]
          assert "topic_finalized" in anchor_types
          assert "topic_initial" in anchor_types

      def test_topic_finalized_has_file_paths(self):
          plugin = TopicPlugin()
          tape = Tape()
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          _fire_turn_end(plugin, tape)
          tape.append(_make_tool_call("file_read", "src/billing.py"))
          _fire_turn_end(plugin, tape)
          finalized = [e for e in tape if e.meta.get("anchor_type") == "topic_finalized"]
          assert len(finalized) == 1
          assert "src/auth.py" in finalized[0].meta["file_paths"]

      def test_topic_initial_has_new_file_paths(self):
          plugin = TopicPlugin()
          tape = Tape()
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          _fire_turn_end(plugin, tape)
          tape.append(_make_tool_call("file_read", "src/billing.py"))
          _fire_turn_end(plugin, tape)
          initial = [e for e in tape if e.meta.get("anchor_type") == "topic_initial"]
          assert len(initial) == 1
          assert "src/billing.py" in initial[0].meta["file_paths"]

      def test_topic_id_is_consistent_within_topic(self):
          """topic_finalized of old topic and topic_initial of new topic have different topic_ids."""
          plugin = TopicPlugin()
          tape = Tape()
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          _fire_turn_end(plugin, tape)
          tape.append(_make_tool_call("file_read", "src/billing.py"))
          _fire_turn_end(plugin, tape)
          finalized = [e for e in tape if e.meta.get("anchor_type") == "topic_finalized"][0]
          initial = [e for e in tape if e.meta.get("anchor_type") == "topic_initial"][0]
          # Different topics → different topic_ids
          assert finalized.meta["topic_id"] != initial.meta["topic_id"]

      def test_no_tool_calls_in_turn_no_boundary(self):
          """Turn with no tool_calls (e.g. pure message turn) is ignored for topic detection."""
          plugin = TopicPlugin()
          tape = Tape()
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          _fire_turn_end(plugin, tape)
          # Second turn: no tool calls
          tape.append(_make_message("user", "thanks"))
          tape.append(_make_message("assistant", "welcome"))
          _fire_turn_end(plugin, tape)
          anchors = [e for e in tape if e.kind == "anchor"]
          assert len(anchors) == 0

      def test_partial_overlap_no_boundary(self):
          """Partial overlap (some shared files) → same topic, no boundary."""
          plugin = TopicPlugin()
          tape = Tape()
          tape.append(_make_tool_call("file_read", "src/auth.py"))
          tape.append(_make_tool_call("file_read", "src/utils.py"))
          _fire_turn_end(plugin, tape)
          tape.append(_make_tool_call("file_read", "src/auth.py"))  # shared
          tape.append(_make_tool_call("file_read", "src/models.py"))
          _fire_turn_end(plugin, tape)
          anchors = [e for e in tape if e.kind == "anchor"]
          assert len(anchors) == 0


  class TestTopicPluginFileExtraction:
      def test_extracts_path_from_arguments_dict(self):
          plugin = TopicPlugin()
          entry = Entry(
              kind="tool_call",
              payload={"name": "file_read", "arguments": {"path": "src/foo.py"}},
          )
          paths = plugin._extract_file_paths([entry])
          assert "src/foo.py" in paths

      def test_extracts_path_from_arguments_json_string(self):
          import json
          plugin = TopicPlugin()
          entry = Entry(
              kind="tool_call",
              payload={
                  "name": "file_read",
                  "arguments": json.dumps({"path": "src/bar.py"}),
              },
          )
          paths = plugin._extract_file_paths([entry])
          assert "src/bar.py" in paths

      def test_ignores_non_file_tool_calls(self):
          plugin = TopicPlugin()
          entry = Entry(
              kind="tool_call",
              payload={"name": "bash", "arguments": {"command": "ls"}},
          )
          paths = plugin._extract_file_paths([entry])
          assert len(paths) == 0
  ```

- [ ] **Step 3: Run to verify tests fail**

  ```bash
  python3 -m pytest tests/coding_agent/plugins/test_topic.py -v
  ```

  Expected: `ImportError` or `ModuleNotFoundError` — `topic.py` does not exist yet.

- [ ] **Step 4: Implement TopicPlugin**

  Create `src/coding_agent/plugins/topic.py`:

  ```python
  """TopicPlugin — zero-LLM topic boundary detection via file-path overlap.

  Listens on on_session_event. On each turn_end, compares the current turn's
  tool_call file paths against the previous turn's file paths.

  Boundary rule: if both sets are non-empty AND disjoint (zero intersection),
  a new topic has begun. TopicPlugin then:
    1. Appends a topic_finalized anchor for the closing topic.
    2. Appends a topic_initial anchor for the new topic.

  Both anchors carry:
    meta["anchor_type"]: "topic_finalized" | "topic_initial"
    meta["topic_id"]: stable UUID for this topic
    meta["file_paths"]: list of file paths associated with this topic
  """

  from __future__ import annotations

  import json
  import uuid
  from typing import Any, Callable

  from agentkit.tape.models import Entry
  from agentkit.tape.tape import Tape


  # File-bearing tool argument keys (checked in order).
  _PATH_KEYS = ("path", "file_path", "filepath", "filename", "target")

  # Tool names whose arguments are unlikely to contain code file paths.
  _NON_FILE_TOOLS = frozenset(
      {"bash", "shell", "run_command", "execute_command", "grep", "glob"}
  )


  class TopicPlugin:
      """Plugin implementing topic boundary detection via file-path overlap heuristic."""

      state_key = "topic"

      def __init__(self) -> None:
          # File paths seen in the PREVIOUS turn (after boundary detection).
          self._prev_turn_paths: set[str] = set()
          # File paths seen in the CURRENT turn (accumulated across appends).
          self._curr_turn_paths: set[str] = set()
          # Tape index of the start of the current turn (for slicing).
          self._turn_start_index: int = 0
          # UUID for the active topic.
          self._current_topic_id: str = str(uuid.uuid4())

      def hooks(self) -> dict[str, Callable[..., Any]]:
          return {"on_session_event": self.on_session_event}

      def on_session_event(
          self,
          event_type: str | None = None,
          payload: dict[str, Any] | None = None,
          **kwargs: Any,
      ) -> None:
          if event_type != "turn_end":
              return
          if payload is None:
              return
          tape: Tape | None = payload.get("tape")
          if tape is None:
              return
          self._process_turn_end(tape)

      def _process_turn_end(self, tape: Tape) -> None:
          """Inspect entries appended this turn, update topic state."""
          all_entries = list(tape)
          turn_entries = all_entries[self._turn_start_index:]

          # Collect file paths from tool_calls in this turn.
          tool_entries = [e for e in turn_entries if e.kind == "tool_call"]
          curr_paths = self._extract_file_paths(tool_entries)

          if curr_paths:
              self._curr_turn_paths = curr_paths

          # Detect boundary: both sides non-empty and disjoint.
          if (
              self._prev_turn_paths
              and self._curr_turn_paths
              and self._prev_turn_paths.isdisjoint(self._curr_turn_paths)
          ):
              # Close the previous topic.
              old_topic_id = self._current_topic_id
              tape.append(
                  Entry(
                      kind="anchor",
                      payload={
                          "content": (
                              f"[Topic closed — {len(self._prev_turn_paths)} file(s): "
                              + ", ".join(sorted(self._prev_turn_paths))
                              + "]"
                          )
                      },
                      meta={
                          "anchor_type": "topic_finalized",
                          "topic_id": old_topic_id,
                          "file_paths": sorted(self._prev_turn_paths),
                      },
                  )
              )
              # Open the new topic.
              new_topic_id = str(uuid.uuid4())
              self._current_topic_id = new_topic_id
              tape.append(
                  Entry(
                      kind="anchor",
                      payload={
                          "content": (
                              f"[New topic — {len(self._curr_turn_paths)} file(s): "
                              + ", ".join(sorted(self._curr_turn_paths))
                              + "]"
                          )
                      },
                      meta={
                          "anchor_type": "topic_initial",
                          "topic_id": new_topic_id,
                          "file_paths": sorted(self._curr_turn_paths),
                      },
                  )
              )

          # Advance state for next turn.
          if curr_paths:
              self._prev_turn_paths = curr_paths
          self._curr_turn_paths = set()
          self._turn_start_index = len(list(tape))

      def _extract_file_paths(self, tool_entries: list[Entry]) -> set[str]:
          """Extract file paths from tool_call entries.

          Only considers entries whose tool name is NOT in _NON_FILE_TOOLS.
          Looks for path-like values in the arguments dict.
          """
          paths: set[str] = set()
          for entry in tool_entries:
              name = entry.payload.get("name", "")
              if name in _NON_FILE_TOOLS:
                  continue
              args = entry.payload.get("arguments", {})
              if isinstance(args, str):
                  try:
                      args = json.loads(args)
                  except (json.JSONDecodeError, ValueError):
                      continue
              if not isinstance(args, dict):
                  continue
              for key in _PATH_KEYS:
                  val = args.get(key)
                  if isinstance(val, str) and val:
                      # Accept anything that looks like a file path (has an extension
                      # or contains a slash and looks like a path).
                      if "." in val.split("/")[-1] or "/" in val:
                          paths.add(val)
                          break
          return paths
  ```

- [ ] **Step 5: Run the topic tests to verify they pass**

  ```bash
  python3 -m pytest tests/coding_agent/plugins/test_topic.py -v
  ```

  Expected: All tests PASS.

- [ ] **Step 6: Run the full test suite to confirm no regression**

  ```bash
  python3 -m pytest tests/ -q --no-header
  ```

  Expected: Same number of failures as before (807 pass, 4 pre-existing failures unchanged).

- [ ] **Step 7: Commit**

  ```bash
  git add src/coding_agent/plugins/topic.py tests/coding_agent/plugins/test_topic.py
  git commit -m "feat(coding-agent): add TopicPlugin with file-path overlap topic detection"
  ```

---

## Task 4: ContextBuilder anchor-type-aware rendering

**Files:**
- Modify: `src/agentkit/context/builder.py`
- Create: `tests/agentkit/context/test_builder_anchor_folding.py`

### Design

Currently `ContextBuilder._entry_to_message` renders ALL `anchor` entries as `{"role": "system", "content": ...}`. After this task it will differentiate:

| `meta["anchor_type"]` | Rendered as |
|---|---|
| `"handoff"` (existing summarizer) | `{"role": "system", "content": "[CONTEXT SUMMARY] ..."}` |
| `"topic_initial"` | `{"role": "system", "content": "[TOPIC START] ..."}` |
| `"topic_finalized"` | `{"role": "system", "content": "[TOPIC END] ..."}` |
| *(absent / unknown)* | `{"role": "system", "content": "..."}` (unchanged, backward compat) |

- [ ] **Step 1: Write the failing tests**

  Create `tests/agentkit/context/test_builder_anchor_folding.py`:

  ```python
  import pytest
  from agentkit.context.builder import ContextBuilder
  from agentkit.tape.tape import Tape
  from agentkit.tape.models import Entry


  def _builder() -> ContextBuilder:
      return ContextBuilder(system_prompt="sys")


  def _anchor(anchor_type: str | None, content: str) -> Entry:
      meta = {"anchor_type": anchor_type} if anchor_type is not None else {}
      return Entry(kind="anchor", payload={"content": content}, meta=meta)


  class TestAnchorFolding:
      def test_anchor_no_type_renders_as_system(self):
          builder = _builder()
          tape = Tape()
          tape.append(_anchor(None, "plain anchor"))
          msgs = builder.build(tape)
          system_contents = [m["content"] for m in msgs if m["role"] == "system"]
          assert "plain anchor" in system_contents

      def test_handoff_anchor_gets_prefix(self):
          builder = _builder()
          tape = Tape()
          tape.append(_anchor("handoff", "summary text"))
          msgs = builder.build(tape)
          system_contents = [m["content"] for m in msgs if m["role"] == "system"]
          assert any("[CONTEXT SUMMARY]" in c for c in system_contents)
          assert any("summary text" in c for c in system_contents)

      def test_topic_initial_anchor_gets_prefix(self):
          builder = _builder()
          tape = Tape()
          tape.append(_anchor("topic_initial", "new topic content"))
          msgs = builder.build(tape)
          system_contents = [m["content"] for m in msgs if m["role"] == "system"]
          assert any("[TOPIC START]" in c for c in system_contents)
          assert any("new topic content" in c for c in system_contents)

      def test_topic_finalized_anchor_gets_prefix(self):
          builder = _builder()
          tape = Tape()
          tape.append(_anchor("topic_finalized", "closed topic content"))
          msgs = builder.build(tape)
          system_contents = [m["content"] for m in msgs if m["role"] == "system"]
          assert any("[TOPIC END]" in c for c in system_contents)
          assert any("closed topic content" in c for c in system_contents)

      def test_unknown_anchor_type_renders_unchanged(self):
          builder = _builder()
          tape = Tape()
          tape.append(_anchor("future_type", "future content"))
          msgs = builder.build(tape)
          system_contents = [m["content"] for m in msgs if m["role"] == "system"]
          assert "future content" in system_contents

      def test_mixed_tape_all_anchors_rendered(self):
          """All anchor types in a single tape all produce system messages."""
          builder = _builder()
          tape = Tape()
          tape.append(Entry(kind="message", payload={"role": "user", "content": "hi"}))
          tape.append(_anchor("handoff", "old summary"))
          tape.append(_anchor("topic_initial", "new topic"))
          tape.append(Entry(kind="message", payload={"role": "assistant", "content": "ok"}))
          tape.append(_anchor("topic_finalized", "topic done"))
          msgs = builder.build(tape)
          system_contents = [m["content"] for m in msgs if m["role"] == "system"]
          assert any("[CONTEXT SUMMARY]" in c for c in system_contents)
          assert any("[TOPIC START]" in c for c in system_contents)
          assert any("[TOPIC END]" in c for c in system_contents)
  ```

- [ ] **Step 2: Run to verify tests fail**

  ```bash
  python3 -m pytest tests/agentkit/context/test_builder_anchor_folding.py -v
  ```

  Expected: `test_handoff_anchor_gets_prefix`, `test_topic_initial_anchor_gets_prefix`, `test_topic_finalized_anchor_gets_prefix` FAIL — no prefix added yet.

- [ ] **Step 3: Update ContextBuilder._entry_to_message**

  In `src/agentkit/context/builder.py`, replace the `elif entry.kind == "anchor":` block:

  ```python
  elif entry.kind == "anchor":
      content = entry.payload.get("content", "")
      anchor_type = entry.meta.get("anchor_type") if entry.meta else None
      if anchor_type == "handoff":
          content = f"[CONTEXT SUMMARY] {content}"
      elif anchor_type == "topic_initial":
          content = f"[TOPIC START] {content}"
      elif anchor_type == "topic_finalized":
          content = f"[TOPIC END] {content}"
      return {"role": "system", "content": content}
  ```

- [ ] **Step 4: Run anchor folding tests to verify they pass**

  ```bash
  python3 -m pytest tests/agentkit/context/test_builder_anchor_folding.py -v
  ```

  Expected: All 6 tests PASS.

- [ ] **Step 5: Run full test suite — confirm no regression**

  ```bash
  python3 -m pytest tests/ -q --no-header
  ```

  Expected: Same passing count as before (807+), 4 pre-existing failures unchanged.

  **Note:** Existing `test_summarizer.py` tests check the anchor's `meta["anchor_type"] == "handoff"` — these should still pass since SummarizerPlugin already sets that field. If any summarizer test fails, it means the anchor rendering change exposed a gap; re-read the failure and fix.

- [ ] **Step 6: Commit**

  ```bash
  git add src/agentkit/context/builder.py tests/agentkit/context/test_builder_anchor_folding.py
  git commit -m "feat(agentkit): differentiate anchor rendering by meta.anchor_type in ContextBuilder"
  ```

---

## Task 5: Final integration smoke test

- [ ] **Step 1: Run the complete test suite one final time**

  ```bash
  python3 -m pytest tests/ -q --no-header 2>&1 | tail -10
  ```

  Expected output (approximate):
  ```
  FAILED tests/test_kb.py::TestKBIndexing::test_index_file_deterministic_ids
  FAILED tests/test_kb.py::TestKBIndexing::test_index_directory
  FAILED tests/test_kb.py::TestKBIndexing::test_index_directory_nested
  FAILED tests/ui/test_security.py::TestCorsHeaders::test_cors_headers_present
  N failed, M passed in Xs
  ```

  Where M > 807 (we added new tests) and the only failures are the same 4 pre-existing ones.

- [ ] **Step 2: Verify the three new files exist**

  ```bash
  test -f src/coding_agent/plugins/topic.py && echo "OK topic.py"
  test -f tests/coding_agent/plugins/test_topic.py && echo "OK test_topic.py"
  test -f tests/agentkit/context/test_builder_anchor_folding.py && echo "OK test_builder_anchor_folding.py"
  ```

  Expected: All three print "OK".

- [ ] **Step 3: Commit all uncommitted P0+P1 changes as a final P0/P1 squash if desired**

  The P0 changes (tape invariants) are currently uncommitted in the working tree alongside the new P1 changes. The recommended approach is to keep them as separate commits (one per task above). If the user wants to squash P0+P1 together, that is their call.

  At minimum, verify git log shows clean commits:

  ```bash
  git log --oneline -6
  ```

---

## Self-Review

**Spec coverage:**
- ④ `on_session_event` hook → Tasks 1 + 2 ✓
- ⑤ TopicPlugin with file-path heuristic → Task 3 ✓
- ⑥ ContextBuilder anchor folding → Task 4 ✓
- Integration smoke → Task 5 ✓

**Placeholder scan:** None found. All steps contain complete code.

**Type consistency:**
- `TopicPlugin._extract_file_paths` returns `set[str]` → consistent with `_prev_turn_paths: set[str]` and `_curr_turn_paths: set[str]`
- `on_session_event` signature uses `event_type: str | None = None, payload: dict[str, Any] | None = None` — consistent across plugin and tests
- `Entry(kind="anchor", meta={"anchor_type": ...})` — consistent with existing SummarizerPlugin and tape.py `load_jsonl` which already reads `meta.get("anchor_type") == "handoff"`

**Dependency order:** Tasks are sequentially ordered. Task 3 Step 1 modifies the Pipeline `on_session_event` payload to include `tape` — this must happen before TopicPlugin is wired in. The plan captures this in Task 3 Step 1 (before writing TopicPlugin tests).
