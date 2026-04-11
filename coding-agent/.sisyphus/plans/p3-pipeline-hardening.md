# P3: Pipeline Hardening

## TL;DR

> **Quick Summary**: Fix the Tape.handoff() windowing bug that loses conversation history, refactor hardcoded anchor_type checks to semantic meta fields, wire DirectiveExecutor with real MemoryRecord handler, and add structlog-based directive tracing for observability.
>
> **Deliverables**:
> - Tape.handoff() correctly preserves recent entries after context folding
> - anchor_type fully replaced by semantic meta fields (is_handoff, fold_boundary) with backward compat
> - DirectiveExecutor wired with MemoryRecord handler, double-write eliminated
> - Structured directive tracing via structlog for pipeline stages and directive execution
>
> **Estimated Effort**: Medium (3 core phases + 1 optional, 8 core tasks + 3 optional)
> **Parallel Execution**: NO — strict sequential phases (1->2->3->4), parallelism WITHIN phases
> **Critical Path**: Phase 1 (bug fix) -> Phase 2 (meta refactor) -> Phase 3 (wiring) -> [Optional: Phase 4 (tracing)]

---

## Context

### Original Request
4 items of work in priority order: fix handoff windowing bug, meta-driven builder refactor, DirectiveExecutor handler wiring, directive tracing. All in one plan, executed sequentially.

### Interview Summary
**Key Discussions**:
- handoff bug: `_window_start = len(entries) - 1` loses recent conversation, fix via window_start param
- anchor_type: simplified removal — only 2 logic consumers remain (summarizer._find_last_finalized, tape.load_jsonl). Replace with fold_boundary/is_handoff. builder.py already uses generic skip/prefix.
- MemoryRecord: handler replaces inline append to eliminate double-write, in-memory only, LanceDB is future work
- Tracing: structlog already in deps, only used in new code, existing loggers untouched

**Research Findings**:
- handoff() has 1 call site (pipeline.py:137), but windowed_entries() has 4 consumers
- builder.py already meta-driven (skip/prefix), rendering layer fully decoupled from anchor_type
- anchor_type only has 2 remaining logic consumers: summarizer._find_last_finalized and tape.load_jsonl
- topic.py sets anchor_type in 2 places (line 112, 147) but builder.py already ignores it via skip/prefix
- _stage_build_context recursively called in tool loops, needs re-entrant safety
- MemoryPlugin.on_turn_end already does inline append, handler would cause double-write

### Metis Review
**Identified Gaps** (addressed):
- anchor_type blast radius smaller than estimated -> only 2 logic consumers, rest are setters/fixtures
- Recursive build_context re-entrant risk -> add re-entrant safety test
- MemoryPlugin double-write -> handler replaces inline append
- structlog already in deps -> no dependency addition needed
- Phase ordering dependency -> strict sequential execution

---

## Work Objectives

### Core Objective
Fix pipeline core bug, improve framework declarative purity, complete directive execution chain, add observability.

### Concrete Deliverables
- `tape.py`: handoff() accepts window_start param, correctly preserves recent entries
- `pipeline.py`: passes resolve_context_window's window_start to handoff()
- `summarizer.py` + `topic.py`: anchor_type removed, semantic meta fields used
- `tape.py:load_jsonl`: backward compat layer for old anchor_type format
- `__main__.py`: DirectiveExecutor wired with memory_handler
- `memory.py`: on_turn_end returns directive instead of inline append
- `agentkit/tracing.py`: structlog config + pipeline/directive tracing

### Definition of Done
- [ ] `uv run pytest tests/ -v` all pass, 0 failures
- [ ] `uv run mypy` on all changed files with no errors
- [ ] Each Phase = one atomic commit with all tests green

### Must Have
- After handoff, windowed_entries() contains anchor + recent entries (not just anchor)
- All anchor_type logic checks replaced with semantic meta fields (fold_boundary, is_handoff)
- load_jsonl reads old-format JSONL (backward compat)
- MemoryRecord handler called, no double-write
- structlog tracing opt-in toggle (default off, no test output noise)

### Must NOT Have (Guardrails)
- Do NOT modify builder.py (already meta-driven)
- Do NOT migrate existing 23 logging.getLogger files to structlog
- Do NOT add Checkpoint producer or handler
- Do NOT add disk/database persistence (in-memory only)
- Do NOT migrate existing 56 JSONL tape files
- agentkit must NOT import coding_agent
- Do NOT add distributed tracing (correlation IDs etc.)
- Do NOT modify ApprovalPlugin or AskUser handler

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest)
- **Automated tests**: TDD (RED -> GREEN -> REFACTOR)
- **Framework**: pytest (uv run pytest)
- **Each task**: Write failing test first, then implement

### QA Policy
Every task includes agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Framework/Library code**: Use Bash (uv run pytest) — run specific test files, assert pass count
- **Integration**: Use Bash (uv run python -c "...") — import and exercise code paths

---

## Execution Strategy

### Parallel Execution Waves

> Phases are SEQUENTIAL (1->2->3->4). Tasks WITHIN a phase can parallelize where noted.

```
Phase 1 — Tape.handoff() Bug Fix (Wave 1):
+-- Task 1: TDD — handoff(window_start) param + tests [deep]
+-- Task 2: Pipeline — pass window_start to handoff + re-entrant safety [deep]
+-- Task 3: load_jsonl roundtrip verification [quick]

Phase 2 — Simplified anchor_type Removal (Wave 2):
+-- Task 4: Replace anchor_type with fold_boundary/is_handoff in topic.py + summarizer.py [deep]
+-- Task 5: tape.py load_jsonl backward compat + is_handoff detection [quick]
+-- Task 6: Update ALL test fixtures across test files [unspecified-high]

Phase 3 — DirectiveExecutor Wiring (Wave 3):
+-- Task 7: MemoryRecord handler + eliminate double-write in MemoryPlugin [deep]
+-- Task 8: Wire handler in __main__.py + integration test [quick]

Phase 4 — Directive Tracing (Wave 4):
+-- Task 9: structlog configuration module [quick]
+-- Task 10: Pipeline stage tracing [deep]
+-- Task 11: DirectiveExecutor tracing [deep]

Wave FINAL (After ALL tasks):
+-- Task F1: Plan compliance audit (oracle)
+-- Task F2: Code quality review (unspecified-high)
+-- Task F3: Real manual QA (unspecified-high)
+-- Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| 1 | — | 2, 3 | 1 |
| 2 | 1 | 3 | 1 |
| 3 | 1, 2 | 4-6 | 1 |
| 4 | 3 | 5, 6 | 2 |
| 5 | 4 | 6 | 2 |
| 6 | 4, 5 | 7-8 | 2 |
| 7 | 6 | 8 | 3 |
| 8 | 7 | 9-11 | 3 |
| 9 | 8 | 10, 11 | 4 |
| 10 | 9 | F1-F4 | 4 |
| 11 | 9 | F1-F4 | 4 |

### Agent Dispatch Summary

- **Phase 1**: 3 tasks — T1 `deep`, T2 `deep`, T3 `quick`
- **Phase 2**: 3 tasks — T4 `deep`, T5 `quick`, T6 `unspecified-high`
- **Phase 3**: 2 tasks — T7 `deep`, T8 `quick`
- **Phase 4**: 3 tasks — T9 `quick`, T10 `deep`, T11 `deep`
- **FINAL**: 4 tasks — F1 `oracle`, F2 `unspecified-high`, F3 `unspecified-high`, F4 `deep`

---

## TODOs

### Phase 1 — Tape.handoff() Bug Fix

- [ ] 1. TDD: Tape.handoff() accepts window_start parameter

  **What to do**:
  - RED: Write failing tests in `tests/agentkit/tape/test_tape.py`:
    - Test: tape with 14 entries, `handoff(anchor, window_start=8)` -> `windowed_entries()` returns entries[8:14] + [anchor] = 7 items
    - Test: `handoff(anchor)` with no window_start -> backward compat, same behavior as before (window starts at anchor)
    - Test: `handoff(anchor, window_start=0)` -> entire tape visible after fold
  - GREEN: Modify `Tape.handoff()` in `src/agentkit/tape/tape.py`:
    - Add `window_start: int | None = None` parameter
    - When `window_start is not None`: set `self._window_start = window_start`
    - When `window_start is None`: keep existing behavior `self._window_start = len(self._entries) - 1`
  - REFACTOR: Ensure lock semantics are preserved, add type hints

  **Must NOT do**:
  - Do NOT touch meta fields or Entry schema
  - Do NOT modify pipeline.py yet (that's Task 2)
  - Do NOT change load_jsonl

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Core data structure bug fix requiring careful reasoning about invariants
  - **Skills**: [`test-driven-development`]
    - `test-driven-development`: TDD workflow (RED-GREEN-REFACTOR)

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1, Sequential first
  - **Blocks**: Tasks 2, 3
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `src/agentkit/tape/tape.py:33-40` — Current `handoff()` and `windowed_entries()` implementation. The bug is at line 40: `self._window_start = len(self._entries) - 1`. Fix: accept window_start param and use it when provided.
  - `src/agentkit/tape/tape.py:19` — `_window_start` initialized to 0. Understand the window semantics: `windowed_entries()` returns `self._entries[self._window_start:]`.

  **Test References**:
  - `tests/agentkit/tape/test_tape.py:86-106` — Existing handoff tests. Follow this pattern for new tests. Current tests assert `windowed_entries()` returns only [anchor] after handoff — this is the BUGGY behavior, new tests should assert correct behavior.
  - `tests/agentkit/tape/test_tape.py:120-151` — Additional handoff/window tests showing how entries are created and asserted.

  **Acceptance Criteria**:

  - [ ] `uv run pytest tests/agentkit/tape/test_tape.py -v -k "handoff"` -> 3+ new tests PASS
  - [ ] Existing handoff tests still pass (may need updating if they assert buggy behavior)
  - [ ] `handoff(anchor, window_start=8)` on 14-entry tape -> `len(windowed_entries()) == 7`

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Handoff preserves recent entries with explicit window_start
    Tool: Bash (uv run pytest)
    Preconditions: Test file has new test_handoff_with_window_start test
    Steps:
      1. Run: uv run pytest tests/agentkit/tape/test_tape.py -v -k "test_handoff_with_window_start"
      2. Assert: exit code 0
      3. Assert: output contains "PASSED"
    Expected Result: Test passes, windowed_entries contains anchor + entries[8:14]
    Failure Indicators: AssertionError showing windowed_entries has wrong length or wrong entries
    Evidence: .sisyphus/evidence/task-1-handoff-window-start.txt

  Scenario: Backward compat — handoff without window_start
    Tool: Bash (uv run pytest)
    Preconditions: Test file has test_handoff_backward_compat test
    Steps:
      1. Run: uv run pytest tests/agentkit/tape/test_tape.py -v -k "test_handoff_backward_compat"
      2. Assert: exit code 0
    Expected Result: Without window_start, behavior unchanged (window at anchor position)
    Failure Indicators: Test failure showing behavior change for existing callers
    Evidence: .sisyphus/evidence/task-1-handoff-backward-compat.txt
  ```

  **Commit**: YES (groups with Tasks 2, 3 in Phase 1 commit)
  - Message: `fix(tape): handoff() accepts window_start to preserve recent entries`
  - Files: `src/agentkit/tape/tape.py`, `tests/agentkit/tape/test_tape.py`

- [ ] 2. Pipeline: pass window_start to handoff + re-entrant safety

  **What to do**:
  - RED: Write failing tests:
    - Test in `tests/agentkit/tape/test_tape.py` or `tests/agentkit/runtime/test_pipeline.py`: After pipeline calls handoff with window_start from resolve_context_window, windowed_entries contains recent entries
    - Test: Re-entrant build_context (called twice in same turn) does NOT double-handoff
  - GREEN: Modify `src/agentkit/runtime/pipeline.py:_stage_build_context`:
    - Currently line ~137: `ctx.tape.handoff(summary_anchor)` ignores `window_start`
    - Fix: `ctx.tape.handoff(summary_anchor, window_start=ctx.tape.window_start + window_start)`
    - The `resolve_context_window` hook returns `(window_start, summary_anchor)` where `window_start` is relative to `windowed_entries()`. Convert to absolute by adding `ctx.tape.window_start`.
  - Add re-entrant guard: if handoff already occurred this turn (anchor already present), skip second handoff

  **Must NOT do**:
  - Do NOT change resolve_context_window hook interface
  - Do NOT modify summarizer plugin logic
  - Do NOT touch meta fields

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Pipeline integration requires understanding of context flow and re-entrancy
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1, Sequential after Task 1
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `src/agentkit/runtime/pipeline.py:129-142` — `_stage_build_context` method. Lines 131-137 call `resolve_context_window` hook, extract `(window_start, summary_anchor)`, but only pass `summary_anchor` to `ctx.tape.handoff()`. The `window_start` value is DISCARDED. Fix: pass it through.
  - `src/agentkit/runtime/pipeline.py:326` — Second call to `_stage_build_context` after tool execution in tool loop. This is the re-entrant call site that could trigger double-handoff.
  - `src/agentkit/runtime/pipeline.py:140,169` — Where `windowed_entries()` is consumed after handoff. These are the places that will benefit from the fix.

  **API/Type References**:
  - `src/agentkit/tape/tape.py:19` — `window_start` property (read-only access to `_window_start`). Needed to compute absolute position.
  - `src/coding_agent/plugins/summarizer.py:34-67` — `resolve_context_window` return type: `tuple[int, Entry] | None`. The int is the split_point RELATIVE to current `windowed_entries()`.

  **Test References**:
  - `tests/agentkit/runtime/test_pipeline.py` — Existing pipeline tests. Check how pipeline is instantiated with mocks for testing.

  **Acceptance Criteria**:

  - [ ] `uv run pytest tests/agentkit/ -v` -> ALL PASS
  - [ ] Pipeline passes window_start to handoff (verify by reading pipeline.py diff)
  - [ ] Re-entrant build_context does not produce duplicate anchors

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Pipeline preserves recent entries after handoff
    Tool: Bash (uv run pytest)
    Preconditions: Pipeline test with mock summarizer returning (split_point=3, anchor)
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline.py -v -k "handoff"
      2. Assert: exit code 0
      3. Assert: windowed_entries after build_context contains entries[3:] + [anchor]
    Expected Result: Recent entries preserved, not just anchor
    Failure Indicators: windowed_entries returns only [anchor]
    Evidence: .sisyphus/evidence/task-2-pipeline-handoff.txt

  Scenario: Re-entrant build_context safety
    Tool: Bash (uv run pytest)
    Preconditions: Test calls _stage_build_context twice on same ctx
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline.py -v -k "reentrant"
      2. Assert: only one anchor in tape entries after two build_context calls
    Expected Result: Second call is no-op, no duplicate anchor
    Failure Indicators: Two anchors found in tape
    Evidence: .sisyphus/evidence/task-2-reentrant-safety.txt
  ```

  **Commit**: YES (groups with Phase 1 commit)
  - Files: `src/agentkit/runtime/pipeline.py`, `tests/agentkit/runtime/test_pipeline.py`

- [ ] 3. load_jsonl roundtrip verification

  **What to do**:
  - RED: Write test that creates a tape, does handoff with window_start, saves to JSONL, loads back, and verifies windowed_entries is correct after reload
  - GREEN: Verify `load_jsonl` in `tape.py` correctly reconstructs `_window_start` after the handoff signature change. The current `load_jsonl` checks `anchor_type == "handoff"` to set window_start — this should still work since we haven't changed meta fields yet (that's Phase 2).
  - If test passes without code changes, that's fine — this is a verification task

  **Must NOT do**:
  - Do NOT change load_jsonl logic (Phase 2 will handle meta changes)
  - Do NOT modify meta field names

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Verification/roundtrip test, minimal code changes expected
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1, after Task 2
  - **Blocks**: Tasks 4-7 (Phase 2 start)
  - **Blocked By**: Tasks 1, 2

  **References**:

  **Pattern References**:
  - `src/agentkit/tape/tape.py:80-100` — `load_jsonl` method. Line 93 checks `anchor_type == "handoff"` to detect handoff anchors and set window_start during deserialization. This logic must still work correctly after Task 1's changes.
  - `src/agentkit/tape/tape.py:70-78` — `save_jsonl` method. Understand serialization format.

  **Test References**:
  - `tests/agentkit/tape/test_tape.py:134-151` — Existing JSONL roundtrip tests. Follow this pattern.

  **Acceptance Criteria**:

  - [ ] Roundtrip test: create tape -> handoff(anchor, window_start=N) -> save_jsonl -> load_jsonl -> windowed_entries matches
  - [ ] `uv run pytest tests/agentkit/tape/test_tape.py -v` -> ALL PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: JSONL roundtrip preserves window_start after handoff
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/tape/test_tape.py -v -k "roundtrip"
      2. Assert: exit code 0
    Expected Result: Loaded tape has same windowed_entries as original
    Failure Indicators: window_start mismatch after load, windowed_entries differ
    Evidence: .sisyphus/evidence/task-3-jsonl-roundtrip.txt

  Scenario: load_jsonl with handoff anchor still detects window boundary
    Tool: Bash (uv run python -c "...")
    Steps:
      1. Create temp JSONL with handoff anchor entry (anchor_type="handoff" in meta)
      2. Load via Tape.load_jsonl()
      3. Assert windowed_entries starts from handoff anchor
    Expected Result: Window correctly positioned at handoff anchor
    Failure Indicators: window_start is 0 (ignoring handoff anchor)
    Evidence: .sisyphus/evidence/task-3-load-jsonl-detection.txt
  ```

  **Commit**: YES (groups with Phase 1 commit)
  - Files: `tests/agentkit/tape/test_tape.py`

### Phase 2 — Simplified anchor_type Removal

> **Meta Field Mapping Table** (reference for all Phase 2 tasks):
>
> | Old anchor_type | New meta fields | Created by | Queried by |
> |----------------|----------------|------------|------------|
> | `"handoff"` | `"is_handoff": True` | SummarizerPlugin | tape.py:load_jsonl |
> | `"topic_finalized"` | `"fold_boundary": True` | TopicPlugin | SummarizerPlugin._find_last_finalized |
> | `"topic_initial"` | (removed, no readers) | TopicPlugin | nobody |
>
> **Rendering layer** (skip/prefix) is already decoupled in builder.py — NO changes needed there.
> Phase 2 only touches **semantic/storage consumers** of anchor_type.

- [ ] 4. Replace anchor_type with fold_boundary/is_handoff in topic.py + summarizer.py

  **What to do**:
  - RED: Write/update tests:
    - Test: `_find_last_finalized` finds entries with `fold_boundary: True` (not `anchor_type`)
    - Test: Handoff anchors created by summarizer have `is_handoff: True` (not `anchor_type`)
    - Test: Topic finalized anchors have `fold_boundary: True`, no `anchor_type`
    - Test: Topic initial anchors have no `anchor_type` field
  - GREEN — topic.py changes:
    - Line 112: Remove `"anchor_type": "topic_initial"` from meta dict. Keep `prefix`, `topic_id`, `topic_number`.
    - Line 147: Replace `"anchor_type": "topic_finalized"` with `"fold_boundary": True`. Keep `skip: True`, `topic_id`, `files`.
  - GREEN — summarizer.py changes:
    - Line 74 (`_find_last_finalized`): `entries[i].meta.get("anchor_type") == "topic_finalized"` -> `entries[i].meta.get("fold_boundary")`
    - Line 89: Same pattern — `anchor_type == "topic_finalized"` -> `meta.get("fold_boundary")`
    - Lines 101, 131 (handoff anchor creation): Remove `"anchor_type": "handoff"`, add `"is_handoff": True`. Keep `prefix: "Context Summary"`.
  - REFACTOR: Verify no remaining `anchor_type` references in either file

  **Must NOT do**:
  - Do NOT touch builder.py (already uses skip/prefix for rendering)
  - Do NOT touch load_jsonl (Task 5)
  - Do NOT change summarizer's windowing/folding algorithm
  - Do NOT change the `resolve_context_window` return type

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Cross-file refactor across 2 tightly-coupled files with multiple edit sites
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (first in Phase 2)
  - **Parallel Group**: Wave 2, first
  - **Blocks**: Tasks 5, 6
  - **Blocked By**: Task 3

  **References**:

  **Pattern References**:
  - `src/coding_agent/plugins/topic.py:112` — `topic_initial` anchor: `meta={"anchor_type": "topic_initial", "topic_id": ..., "topic_number": ..., "prefix": "Topic Start"}`. Remove `anchor_type`.
  - `src/coding_agent/plugins/topic.py:147` — `topic_finalized` anchor: `meta={"anchor_type": "topic_finalized", "topic_id": ..., "files": [...], "skip": True}`. Replace `anchor_type` with `"fold_boundary": True`.
  - `src/coding_agent/plugins/summarizer.py:69-77` — `_find_last_finalized`: reverse scan checking `anchor_type == "topic_finalized"`. Change to `meta.get("fold_boundary")`.
  - `src/coding_agent/plugins/summarizer.py:89` — Second `anchor_type == "topic_finalized"` check. Same replacement.
  - `src/coding_agent/plugins/summarizer.py:101` — Handoff anchor creation (topic summary path): `"anchor_type": "handoff"`. Replace with `"is_handoff": True`.
  - `src/coding_agent/plugins/summarizer.py:131` — Handoff anchor creation (entry-count fallback): `"anchor_type": "handoff"`. Replace with `"is_handoff": True`.

  **Context — Why skip/prefix is NOT redundant with fold_boundary/is_handoff**:
  - `skip`/`prefix` = rendering layer (builder.py uses these to control what appears in LLM context)
  - `fold_boundary` = semantic layer (summarizer uses this to detect topic boundaries for folding decisions)
  - `is_handoff` = storage layer (load_jsonl uses this to reconstruct window_start on deserialization)
  - These are orthogonal concerns. An anchor can be `skip: True` AND `fold_boundary: True`.

  **Test References**:
  - `tests/coding_agent/plugins/test_summarizer.py:12,24,66` — Fixtures with `anchor_type`. Update to new meta fields.

  **Acceptance Criteria**:

  - [ ] `topic.py` has zero `anchor_type` references
  - [ ] `summarizer.py` has zero `anchor_type` references
  - [ ] `topic_finalized` anchors have `fold_boundary: True`
  - [ ] Handoff anchors have `is_handoff: True`
  - [ ] `_find_last_finalized` uses `fold_boundary` check
  - [ ] `uv run pytest tests/coding_agent/plugins/ -v` -> PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Summarizer finds fold boundary via fold_boundary meta
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_summarizer.py -v -k "find_last_finalized"
      2. Assert: exit code 0
    Expected Result: _find_last_finalized correctly identifies entries with fold_boundary: True
    Failure Indicators: Returns None when fold boundary entries exist
    Evidence: .sisyphus/evidence/task-4-fold-boundary.txt

  Scenario: Zero anchor_type strings in production code
    Tool: Bash (grep)
    Steps:
      1. Run: grep -n "anchor_type" src/coding_agent/plugins/summarizer.py src/coding_agent/plugins/topic.py
      2. Assert: exit code 1 (no matches)
    Expected Result: Zero occurrences of "anchor_type" in either file
    Failure Indicators: grep finds matches
    Evidence: .sisyphus/evidence/task-4-no-anchor-type.txt
  ```

  **Commit**: YES (groups with Phase 2 commit)
  - Files: `src/coding_agent/plugins/topic.py`, `src/coding_agent/plugins/summarizer.py`

- [ ] 5. tape.py load_jsonl backward compat + is_handoff detection

  **What to do**:
  - RED: Write tests:
    - Test: load JSONL with OLD format entries (`anchor_type: "handoff"`) -> `windowed_entries()` correct
    - Test: load JSONL with NEW format entries (`is_handoff: True`) -> `windowed_entries()` correct
    - Test: OLD format entries get `is_handoff` and `fold_boundary` migrated into meta on load
  - GREEN: Modify `src/agentkit/tape/tape.py:load_jsonl` (line 84-100):
    - After loading each entry, add migration logic:
      - If `meta.get("anchor_type") == "handoff"` and `"is_handoff" not in meta`: set `meta["is_handoff"] = True`
      - If `meta.get("anchor_type") == "topic_finalized"` and `"fold_boundary" not in meta`: set `meta["fold_boundary"] = True`
    - Change window detection (line 93): `if entry.meta.get("is_handoff")` instead of `entry.meta.get("anchor_type") == "handoff"`
  - REFACTOR: Keep migration logic minimal, add a brief comment

  **Must NOT do**:
  - Do NOT modify existing JSONL data files on disk
  - Do NOT add a standalone migration script
  - Do NOT remove the compat layer (it stays permanently for old files)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small, focused change in one method
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (after Task 4 which changes meta field names)
  - **Parallel Group**: Wave 2, after Task 4
  - **Blocks**: Task 6
  - **Blocked By**: Task 4

  **References**:

  **Pattern References**:
  - `src/agentkit/tape/tape.py:84-100` — `load_jsonl` method. Line 93: `if entry.meta.get("anchor_type") == "handoff": window_start = i`. Add migration BEFORE this, then change detection to `is_handoff`.

  **Test References**:
  - `tests/agentkit/tape/test_tape.py:134-151` — Existing JSONL roundtrip tests. Add backward compat test alongside.

  **Acceptance Criteria**:

  - [ ] Old-format JSONL (with `anchor_type: "handoff"`) loads correctly, window positioned correctly
  - [ ] New-format JSONL (with `is_handoff: True`) loads correctly
  - [ ] Migration adds `is_handoff`/`fold_boundary` to old entries
  - [ ] Window detection uses `is_handoff` not `anchor_type`
  - [ ] `uv run pytest tests/agentkit/tape/test_tape.py -v` -> PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Old-format JSONL backward compatibility
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/tape/test_tape.py -v -k "backward_compat"
      2. Assert: exit code 0
    Expected Result: Old format entries get migrated meta fields during load
    Failure Indicators: window_start not set correctly, is_handoff missing
    Evidence: .sisyphus/evidence/task-5-backward-compat.txt

  Scenario: Window detection uses is_handoff
    Tool: Bash (grep)
    Steps:
      1. grep -n 'is_handoff' src/agentkit/tape/tape.py
      2. Assert: match found in load_jsonl for window detection
      3. grep -n 'anchor_type.*==.*handoff' src/agentkit/tape/tape.py
      4. Assert: only compat migration line remains, not the window detection line
    Expected Result: Detection logic uses is_handoff, only compat migration references anchor_type
    Failure Indicators: Old-style detection still present
    Evidence: .sisyphus/evidence/task-5-is-handoff-detection.txt
  ```

  **Commit**: YES (groups with Phase 2 commit)
  - Files: `src/agentkit/tape/tape.py`, `tests/agentkit/tape/test_tape.py`

- [ ] 6. Update ALL test fixtures across test files

  **What to do**:
  - Update every test fixture that creates entries with `anchor_type` in meta to use new meta fields:
    - `tests/agentkit/tape/test_tape.py` — ~5 occurrences: `"anchor_type": "handoff"` -> `"is_handoff": True`
    - `tests/coding_agent/plugins/test_summarizer.py` — ~5+ occurrences: update all anchor_type references
    - `tests/agentkit/runtime/test_pipeline.py` — ~2 occurrences: update pipeline test fixtures
  - Mapping: `"anchor_type": "handoff"` -> `"is_handoff": True` | `"anchor_type": "topic_finalized"` -> `"fold_boundary": True` | `"anchor_type": "topic_initial"` -> remove entirely
  - Run FULL test suite to verify no regressions
  - This task is the "sweep" that catches everything Tasks 4-5 didn't cover

  **Must NOT do**:
  - Do NOT change production code (only test files)
  - Do NOT add new test cases (only update existing fixtures)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Many files, systematic search-and-replace across test infrastructure
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Tasks 4, 5 both complete)
  - **Parallel Group**: Wave 2, last
  - **Blocks**: Tasks 7, 8 (Phase 3 start)
  - **Blocked By**: Tasks 4, 5

  **References**:

  **Pattern References**:
  - Use `grep -rn "anchor_type" tests/` to find ALL remaining occurrences
  - Each occurrence: `"anchor_type": "handoff"` -> `"is_handoff": True`, `"anchor_type": "topic_finalized"` -> `"fold_boundary": True`, `"anchor_type": "topic_initial"` -> remove

  **Test References**:
  - `tests/agentkit/tape/test_tape.py:93,114,128,140,151` — 5 fixtures with anchor_type
  - `tests/coding_agent/plugins/test_summarizer.py:12,24,66,125,271` — 5+ fixtures
  - `tests/agentkit/runtime/test_pipeline.py:271,289` — 2 fixtures

  **Acceptance Criteria**:

  - [ ] `grep -rn "anchor_type" tests/` -> only matches in backward-compat test fixtures (Task 5's old-format JSONL tests)
  - [ ] No `anchor_type` references remain in non-backward-compat test fixtures (topic_initial, topic_finalized used as old meta keys are removed)
  - [ ] `uv run pytest tests/ -v --tb=short` -> ALL PASS, 0 failures
  - [ ] Total test count >= 841 (no tests deleted, baseline is 841)

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: anchor_type only in backward-compat test fixtures
    Tool: Bash (grep)
    Steps:
      1. Run: grep -rn "anchor_type" tests/
      2. Assert: matches exist ONLY in backward-compat tests (test_tape.py backward_compat test functions)
      3. Assert: zero matches in test_summarizer.py, test_pipeline.py non-compat fixtures
    Expected Result: anchor_type only appears in tests that validate old-format JSONL loading
    Failure Indicators: anchor_type found in non-compat fixtures (e.g., test_summarizer creating new anchors with anchor_type)
    Evidence: .sisyphus/evidence/task-6-anchor-type-compat-only.txt

  Scenario: Full test suite green after fixture updates
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/ -v --tb=short 2>&1 | tail -20
      2. Assert: "failed" not in output
      3. Assert: test count >= 841
    Expected Result: All tests pass, no regressions
    Failure Indicators: Any test failure
    Evidence: .sisyphus/evidence/task-6-full-suite.txt
  ```

  **Commit**: YES (groups with Phase 2 commit)
  - Files: `tests/agentkit/tape/test_tape.py`, `tests/coding_agent/plugins/test_summarizer.py`, `tests/agentkit/runtime/test_pipeline.py`

### Phase 3 — DirectiveExecutor Handler Wiring

> **Confirmed**: The pipeline already collects directive return values from `on_turn_end` hooks.
> - `pipeline.py:335` → `directives = self._runtime.call_many("on_turn_end", tape=ctx.tape)`
> - `hook_runtime.py:43-61` → `call_many` aggregates non-None return values from all plugins
> - `pipeline.py:338-341` → loops through and executes each directive via `DirectiveExecutor.execute()`
> So returning `MemoryRecord` from `on_turn_end` will automatically get collected and executed. No pipeline changes needed.

- [ ] 7. MemoryRecord handler + eliminate double-write in MemoryPlugin

  **What to do**:
  - RED: Write failing tests:
    - Test: MemoryPlugin.on_turn_end returns MemoryRecord directive (not None)
    - Test: MemoryPlugin.on_turn_end does NOT append to self._memories inline (no double-write)
    - Test: A mock handler receives the MemoryRecord directive with correct fields
  - GREEN:
    - Modify `src/coding_agent/plugins/memory.py:on_turn_end`:
      - REMOVE the inline `self._memories.append(...)` at ~line 153-159
      - RETURN a `MemoryRecord(summary=..., tags=..., importance=...)` directive instead
      - The handler callback (wired in Task 8) will be responsible for persistence
     - Create a handler function (e.g., in `src/coding_agent/handlers.py` or inline in `__main__.py`):
      - `async def memory_handler(directive: MemoryRecord) -> None:` that appends to the MemoryPlugin's memory list
      - **Prefer using a public method** (e.g., `plugin.add_memory(record)`) over directly accessing `plugin._memories`. If no public method exists, add a simple one rather than coupling to internals via closure.
      - The handler needs access to the MemoryPlugin instance — pass via closure or lookup
  - REFACTOR: Ensure the MemoryRecord contains all fields the old inline append used

  **Must NOT do**:
  - Do NOT add disk persistence
  - Do NOT change MemoryRecord dataclass definition
  - Do NOT touch Checkpoint handling
  - Do NOT import coding_agent from agentkit

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Refactoring the persistence flow, understanding directive lifecycle
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (first in Phase 3)
  - **Parallel Group**: Wave 3, first
  - **Blocks**: Task 8
  - **Blocked By**: Task 6

  **References**:

  **Pattern References**:
  - `src/coding_agent/plugins/memory.py:140-165` — `on_turn_end` method. Lines 153-159 do inline `self._memories.append({"summary": ..., "tags": ..., "importance": ...})`. This is the double-write source. Replace with `return MemoryRecord(summary=..., tags=..., importance=...)`.
  - `src/agentkit/directive/types.py` — `MemoryRecord` dataclass definition. Check fields: `summary: str`, `tags: list[str]`, `importance: float`. Ensure on_turn_end populates these correctly.
  - `src/agentkit/directive/executor.py:50-57` — How executor handles MemoryRecord: calls `self._memory_handler(directive)` if handler exists, else logs debug skip. This is the receiving end.

  **Test References**:
  - `tests/coding_agent/plugins/test_memory.py` — Existing memory tests. Add new tests for directive return.

  **Acceptance Criteria**:

  - [ ] `on_turn_end` returns `MemoryRecord` directive (not None)
  - [ ] `self._memories.append(...)` removed from `on_turn_end`
  - [ ] Handler function created that receives MemoryRecord and persists to memory list
  - [ ] `uv run pytest tests/coding_agent/plugins/test_memory.py -v` -> PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: on_turn_end returns MemoryRecord directive
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/coding_agent/plugins/test_memory.py -v -k "directive"
      2. Assert: exit code 0
    Expected Result: on_turn_end returns MemoryRecord with correct summary/tags/importance
    Failure Indicators: Returns None, or still does inline append
    Evidence: .sisyphus/evidence/task-7-memory-directive.txt

  Scenario: No inline self._memories.append in on_turn_end
    Tool: Bash (grep)
    Steps:
      1. Run: grep -n "self._memories.append" src/coding_agent/plugins/memory.py
      2. Assert: no match found within the on_turn_end method body
      3. Run: grep -A 30 "def on_turn_end" src/coding_agent/plugins/memory.py | grep "self._memories.append"
      4. Assert: exit code 1 (no match — append is not inside on_turn_end)
    Expected Result: self._memories.append no longer called inside on_turn_end; persistence delegated to handler
    Failure Indicators: grep finds self._memories.append within on_turn_end method body
    Evidence: .sisyphus/evidence/task-7-no-double-write.txt
  ```

  **Commit**: YES (groups with Phase 3 commit)
  - Files: `src/coding_agent/plugins/memory.py`, `tests/coding_agent/plugins/test_memory.py`

- [ ] 8. Wire handler in __main__.py + integration test

  **What to do**:
  - RED: Write integration test:
    - Test: Pipeline run with MemoryPlugin -> on_turn_end -> DirectiveExecutor calls memory_handler -> memory persisted
  - GREEN: Modify `src/coding_agent/__main__.py`:
    - Line ~150: Change `DirectiveExecutor(handlers={})` to wire the memory_handler
    - Create or import the handler function from Task 7
    - Wire: `DirectiveExecutor(memory_handler=memory_handler_fn)`
    - The handler needs access to MemoryPlugin's `_memories` list (via closure capturing the plugin instance)
  - REFACTOR: Ensure handler access pattern is clean

  **Must NOT do**:
  - Do NOT add new directive types
  - Do NOT wire Checkpoint handler (no producer)
  - Do NOT change AskUser handling (already works in REPL mode)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small wiring change in __main__.py
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3, after Task 7
  - **Blocks**: Tasks 9-11 (Phase 4 start)
  - **Blocked By**: Task 7

  **References**:

  **Pattern References**:
  - `src/coding_agent/__main__.py:145-155` — DirectiveExecutor instantiation. Currently `DirectiveExecutor()` or `DirectiveExecutor(handlers={})`. Wire memory_handler here.
  - `src/agentkit/directive/executor.py:15-25` — DirectiveExecutor.__init__ signature. Check how handlers are passed (keyword args or dict).

  **Test References**:
  - `tests/agentkit/directive/` — If exists, check for executor test patterns.
  - `tests/coding_agent/plugins/test_memory.py` — Integration test can go here or in new file.

  **Acceptance Criteria**:

  - [ ] `__main__.py` passes `memory_handler` to DirectiveExecutor
  - [ ] Integration test: mock pipeline run -> MemoryRecord -> handler called -> memory stored
  - [ ] `uv run pytest tests/ -v --tb=short` -> ALL PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: DirectiveExecutor has memory_handler wired
    Tool: Bash (grep)
    Steps:
      1. grep -n "memory_handler" src/coding_agent/__main__.py
      2. Assert: match found showing handler wired
    Expected Result: memory_handler passed to DirectiveExecutor
    Failure Indicators: No match, or handlers={} still empty
    Evidence: .sisyphus/evidence/task-8-handler-wired.txt

  Scenario: End-to-end directive flow works
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/ -v -k "memory_handler or directive_wiring"
      2. Assert: exit code 0
    Expected Result: MemoryRecord flows from plugin to handler
    Failure Indicators: Handler never called, directive silently dropped
    Evidence: .sisyphus/evidence/task-8-e2e-directive.txt
  ```

  **Commit**: YES (groups with Phase 3 commit)
  - Message: `feat(directive): wire MemoryRecord handler, eliminate double-write`
  - Files: `src/coding_agent/__main__.py`, integration test file

### Phase 4 — Directive Tracing (structlog) — ⚠️ OPTIONAL / DEFERRED

> **This phase has no direct impact on self-iteration functionality.**
> Execute Phase 1-3 first. Phase 4 can be done as a separate follow-up if/when tracing becomes needed.
> If executing: Tasks 9-11 are still fully specified below. Simply skip them if deferring.

- [ ] 9. structlog configuration module

  **What to do**:
  - RED: Write test:
    - Test: `configure_tracing()` sets up structlog with JSON output
    - Test: `configure_tracing(enabled=False)` is a no-op (no output)
    - Test: Tracing logger produces parseable JSON
  - GREEN: Create `src/agentkit/tracing.py`:
    - `configure_tracing(enabled: bool = False, level: str = "INFO") -> None` — configures structlog
    - Use structlog processors: add_log_level, TimeStamper, JSONRenderer (or ConsoleRenderer for dev)
    - `get_tracer(name: str) -> structlog.BoundLogger` — returns a named logger
    - Environment-aware: `AGENTKIT_TRACING=1` env var enables tracing, or explicit `enabled=True`
  - REFACTOR: Keep it minimal, no custom processors yet

  **Must NOT do**:
  - Do NOT migrate existing loggers
  - Do NOT add custom processors beyond basics
  - Do NOT add correlation IDs or span tracking

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small standalone module, well-defined scope
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (first in Phase 4)
  - **Parallel Group**: Wave 4, first
  - **Blocks**: Tasks 10, 11
  - **Blocked By**: Task 8

  **References**:

  **External References**:
  - structlog docs: https://www.structlog.org/en/stable/configuration.html — Configuration API
  - structlog is already in `pyproject.toml:11` as `"structlog>=24.0.0"` — no dependency to add

  **Pattern References**:
  - `src/agentkit/` — Existing module structure. New file goes at `src/agentkit/tracing.py`.

  **Acceptance Criteria**:

  - [ ] `from agentkit.tracing import configure_tracing, get_tracer` imports successfully
  - [ ] `configure_tracing(enabled=True)` sets up structlog
  - [ ] `get_tracer("test").info("hello")` produces JSON output
  - [ ] `configure_tracing(enabled=False)` produces no output
  - [ ] `uv run pytest tests/agentkit/ -v` -> PASS (tracing disabled by default, no noise)

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Tracing module imports and configures
    Tool: Bash (uv run python -c "...")
    Steps:
      1. Run: uv run python -c "from agentkit.tracing import configure_tracing, get_tracer; configure_tracing(enabled=True); log = get_tracer('test'); log.info('hello', key='value')"
      2. Assert: output contains JSON with "key": "value" and "event": "hello"
    Expected Result: JSON-formatted structured log output
    Failure Indicators: ImportError, no output, or unstructured output
    Evidence: .sisyphus/evidence/task-9-tracing-config.txt

  Scenario: Tracing disabled produces no output
    Tool: Bash (uv run python -c "...")
    Steps:
      1. Run: uv run python -c "from agentkit.tracing import configure_tracing, get_tracer; configure_tracing(enabled=False); log = get_tracer('test'); log.info('should not appear')"
      2. Assert: empty or no structlog output
    Expected Result: No tracing output when disabled
    Failure Indicators: Output appears when tracing is disabled
    Evidence: .sisyphus/evidence/task-9-tracing-disabled.txt
  ```

  **Commit**: YES (groups with Phase 4 commit)
  - Files: `src/agentkit/tracing.py`, `tests/agentkit/test_tracing.py`

- [ ] 10. Pipeline stage tracing

  **What to do**:
  - RED: Write test:
    - Test: With tracing enabled, running pipeline stages produces trace events with stage name, duration, entry count
    - Test: With tracing disabled, no trace events produced
  - GREEN: Modify `src/agentkit/runtime/pipeline.py`:
    - Import `get_tracer` from `agentkit.tracing`
    - Create module-level tracer: `tracer = get_tracer("agentkit.pipeline")`
    - In `run_turn` (line ~79-100): wrap each stage with trace:
      ```python
      tracer.info("stage_start", stage=stage_name, entry_count=len(ctx.tape))
      # ... execute stage ...
      tracer.info("stage_end", stage=stage_name, duration_ms=elapsed, entry_count=len(ctx.tape))
      ```
    - In `_stage_build_context`: trace handoff event with window_start and anchor info
    - In tool loop: trace each tool call approval/rejection
  - REFACTOR: Keep trace points minimal (start/end per stage, key events only)

  **Must NOT do**:
  - Do NOT change pipeline logic
  - Do NOT add timing to hooks themselves (only stages)
  - Do NOT replace existing logger.info/debug calls

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Understanding pipeline flow to place trace points correctly
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 11, after Task 9)
  - **Parallel Group**: Wave 4, parallel with Task 11
  - **Blocks**: F1-F4
  - **Blocked By**: Task 9

  **References**:

  **Pattern References**:
  - `src/agentkit/runtime/pipeline.py:79-105` — `run_turn` method with stage loop. Each stage is a method call. Wrap with timing.
  - `src/agentkit/runtime/pipeline.py:129-142` — `_stage_build_context` with handoff. Trace the handoff event.
  - `src/agentkit/runtime/pipeline.py:240-260` — Tool approval section. Trace approve/reject decisions.
  - `src/agentkit/runtime/pipeline.py:330-341` — `on_turn_end` directive execution. Trace directive collection.

  **Acceptance Criteria**:

  - [ ] Pipeline stages produce structlog trace events when tracing enabled
  - [ ] Each trace event includes: stage name, duration_ms, entry_count
  - [ ] Handoff event traced with window_start info
  - [ ] No trace output when tracing disabled
  - [ ] `uv run pytest tests/ -v --tb=short` -> ALL PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Pipeline produces stage trace events
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/test_tracing.py -v -k "pipeline_stage"
      2. Assert: exit code 0
    Expected Result: Trace events captured with stage_start/stage_end, duration, entry count
    Failure Indicators: No trace events, missing fields
    Evidence: .sisyphus/evidence/task-10-pipeline-tracing.txt

  Scenario: Tracing disabled means no pipeline trace output
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/runtime/test_pipeline.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-10-no-trace-noise.txt
      2. Assert: exit code 0 (pipeline tests pass without tracing)
      3. Run: grep -c "structlog\|stage_start\|stage_end\|directive_execute" .sisyphus/evidence/task-10-no-trace-noise.txt
      4. Assert: count is 0 (no trace output leaked into test output)
    Expected Result: Pipeline tests produce clean output with no structlog trace events
    Failure Indicators: structlog JSON or trace event strings appear in test output
    Evidence: .sisyphus/evidence/task-10-no-trace-noise.txt
  ```

  **Commit**: YES (groups with Phase 4 commit)
  - Files: `src/agentkit/runtime/pipeline.py`, `tests/agentkit/test_tracing.py`

- [ ] 11. DirectiveExecutor tracing

  **What to do**:
  - RED: Write test:
    - Test: DirectiveExecutor.execute() with tracing enabled produces trace event with directive_type, handler_present, result
    - Test: Trace captures both successful handler calls and fallback (no handler) cases
  - GREEN: Modify `src/agentkit/directive/executor.py`:
    - Import `get_tracer` from `agentkit.tracing`
    - Create tracer: `tracer = get_tracer("agentkit.directive")`
    - In `execute()` method: trace before/after dispatch:
      ```python
      tracer.info("directive_execute",
          directive_type=type(directive).__name__,
          handler_present=bool(handler),
          result=result)
      ```
    - Trace fallback cases (no handler, default behavior)
  - REFACTOR: Ensure trace output is useful for debugging "why was my tool call rejected?"

  **Must NOT do**:
  - Do NOT change executor dispatch logic
  - Do NOT add new directive types
  - Do NOT migrate existing logging in executor

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Understanding executor dispatch flow for correct trace placement
  - **Skills**: [`test-driven-development`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 10, after Task 9)
  - **Parallel Group**: Wave 4, parallel with Task 10
  - **Blocks**: F1-F4
  - **Blocked By**: Task 9

  **References**:

  **Pattern References**:
  - `src/agentkit/directive/executor.py:35-57` — `execute()` method. Lines 36-57 dispatch based on directive type. Add trace before match statement and after result.
  - `src/agentkit/directive/executor.py:40-42` — Approve handler (always True). Trace: "directive_execute, type=Approve, result=True".
  - `src/agentkit/directive/executor.py:43-46` — Reject handler (logs reason, returns False). Trace: "directive_execute, type=Reject, reason=..., result=False".
  - `src/agentkit/directive/executor.py:47-52` — AskUser handler (calls handler or fallback). Trace: "directive_execute, type=AskUser, handler_present=bool, result=bool".

  **Acceptance Criteria**:

  - [ ] DirectiveExecutor produces trace events for each directive execution
  - [ ] Trace includes: directive_type, handler_present, result
  - [ ] Reject traces include reason field
  - [ ] No trace output when tracing disabled
  - [ ] `uv run pytest tests/ -v --tb=short` -> ALL PASS

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Executor traces directive dispatch
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/test_tracing.py -v -k "directive"
      2. Assert: exit code 0
    Expected Result: Trace events for Approve/Reject/AskUser with correct fields
    Failure Indicators: Missing trace events, wrong directive_type
    Evidence: .sisyphus/evidence/task-11-directive-tracing.txt

  Scenario: Reject directive trace includes reason
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/test_tracing.py -v -k "reject" 2>&1 | tee .sisyphus/evidence/task-11-reject-reason.txt
      2. Assert: exit code 0
      3. Assert: output contains "PASSED"
      4. Assert: test verifies trace event has directive_type="Reject" and reason="dangerous operation"
    Expected Result: Dedicated test for Reject reason tracing passes, confirming reason field is captured
    Failure Indicators: Test fails, reason missing from captured trace event
    Evidence: .sisyphus/evidence/task-11-reject-reason.txt
  ```

  **Commit**: YES (groups with Phase 4 commit)
  - Message: `feat(tracing): add structlog directive tracing for pipeline and executor`
  - Files: `src/agentkit/directive/executor.py`, `tests/agentkit/test_tracing.py`

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Must Have — handoff preserves recent entries
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/agentkit/tape/test_tape.py -v -k "handoff_with_window_start"
      2. Assert: exit code 0
    Expected Result: Test passes confirming windowed_entries includes recent entries after handoff
    Failure Indicators: Test not found or fails
    Evidence: .sisyphus/evidence/f1-must-have-handoff.txt

  Scenario: Must Have — anchor_type replaced with semantic meta (fold_boundary, is_handoff)
    Tool: Bash (grep)
    Steps:
      1. Run: grep -rn "anchor_type" src/coding_agent/plugins/ src/agentkit/tape/tape.py
      2. Assert: Only backward compat migration line in load_jsonl remains (1 match max for compat)
    Expected Result: No logic checks use anchor_type, only compat migration
    Failure Indicators: Multiple matches showing anchor_type still used for logic
    Evidence: .sisyphus/evidence/f1-must-have-no-anchor-type.txt

  Scenario: Must Have — MemoryRecord handler wired
    Tool: Bash (grep)
    Steps:
      1. Run: grep -n "memory_handler" src/coding_agent/__main__.py
      2. Assert: match found
    Expected Result: memory_handler passed to DirectiveExecutor
    Failure Indicators: No match
    Evidence: .sisyphus/evidence/f1-must-have-memory-handler.txt

  Scenario: Must Have — structlog tracing opt-in
    Tool: Bash (uv run python -c "...")
    Steps:
      1. Run: uv run python -c "from agentkit.tracing import configure_tracing; configure_tracing(enabled=False); print('ok')"
      2. Assert: output is "ok" with no structlog noise
    Expected Result: Tracing disabled by default, no output
    Failure Indicators: ImportError or unexpected output
    Evidence: .sisyphus/evidence/f1-must-have-tracing-opt-in.txt

  Scenario: Must NOT Have — builder.py unchanged
    Tool: Bash (git)
    Steps:
      1. Run: git diff HEAD~4 -- src/agentkit/context/builder.py
      2. Assert: empty diff (no changes)
    Expected Result: builder.py not modified in any of the 4 commits
    Failure Indicators: Diff shows changes
    Evidence: .sisyphus/evidence/f1-must-not-builder.txt

  Scenario: Must NOT Have — no coding_agent import in agentkit
    Tool: Bash (grep)
    Steps:
      1. Run: grep -rn "from coding_agent\|import coding_agent" src/agentkit/
      2. Assert: exit code 1 (no matches)
    Expected Result: Zero imports of coding_agent in agentkit
    Failure Indicators: Any match
    Evidence: .sisyphus/evidence/f1-must-not-import.txt
  ```

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run mypy` on all changed files + `uv run pytest tests/ -v --tb=short`. Review all changed files for: empty catches, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Mypy [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Mypy passes on all changed files
    Tool: Bash (uv run mypy)
    Steps:
      1. Run: uv run mypy src/agentkit/tape/tape.py src/agentkit/runtime/pipeline.py src/agentkit/directive/executor.py src/agentkit/tracing.py src/coding_agent/plugins/summarizer.py src/coding_agent/plugins/topic.py src/coding_agent/plugins/memory.py src/coding_agent/__main__.py --no-error-summary
      2. Assert: exit code 0
    Expected Result: No type errors
    Failure Indicators: Any mypy error
    Evidence: .sisyphus/evidence/f2-mypy.txt

  Scenario: Full test suite passes
    Tool: Bash (uv run pytest)
    Steps:
      1. Run: uv run pytest tests/ -v --tb=short
      2. Assert: exit code 0, 0 failures
      3. Assert: test count >= 841 + new tests
    Expected Result: All tests green
    Failure Indicators: Any failure
    Evidence: .sisyphus/evidence/f2-full-tests.txt

  Scenario: No AI slop patterns in changed files
    Tool: Bash (grep)
    Steps:
      1. Run: grep -rn "TODO\|FIXME\|HACK\|XXX" src/agentkit/tracing.py src/agentkit/tape/tape.py src/agentkit/runtime/pipeline.py src/agentkit/directive/executor.py
      2. Verify: no unresolved TODOs added by implementation
      3. Run: grep -c "^#" on each changed file to check for excessive comments
    Expected Result: Clean code, no stale TODOs, reasonable comment density
    Failure Indicators: New TODOs/FIXMEs, or >30% comment-to-code ratio
    Evidence: .sisyphus/evidence/f2-code-quality.txt
  ```

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state. Execute EVERY QA scenario from EVERY task. Test cross-phase integration (handoff + meta fields + tracing working together). Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | VERDICT`

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Cross-phase integration — handoff + meta fields + tracing
    Tool: Bash (uv run python -c "...")
    Steps:
      1. Configure tracing enabled
      2. Create a Tape with 20 entries
      3. Create a topic_finalized anchor with fold_boundary=True (no anchor_type)
      4. Create a handoff anchor with is_handoff=True (no anchor_type)
      5. Call tape.handoff(anchor, window_start=10)
      6. Assert windowed_entries has entries[10:] + [anchor]
      7. Save to JSONL, reload, assert window preserved
      8. Assert structlog output contains stage trace events
    Expected Result: All phases work together — correct windowing, semantic meta, tracing output
    Failure Indicators: Any assertion failure in the integration chain
    Evidence: .sisyphus/evidence/f3-cross-phase-integration.txt

  Scenario: Re-execute all 22 task QA scenarios
    Tool: Bash (sequential commands)
    Steps:
      Task 1 (2 scenarios):
        1. uv run pytest tests/agentkit/tape/test_tape.py -v -k "test_handoff_with_window_start" 2>&1 | tee .sisyphus/evidence/final-qa/task-1-handoff-window-start.txt
        2. uv run pytest tests/agentkit/tape/test_tape.py -v -k "test_handoff_backward_compat" 2>&1 | tee .sisyphus/evidence/final-qa/task-1-backward-compat.txt
      Task 2 (2 scenarios):
        3. uv run pytest tests/agentkit/runtime/test_pipeline.py -v -k "handoff" 2>&1 | tee .sisyphus/evidence/final-qa/task-2-pipeline-handoff.txt
        4. uv run pytest tests/agentkit/runtime/test_pipeline.py -v -k "reentrant" 2>&1 | tee .sisyphus/evidence/final-qa/task-2-reentrant.txt
      Task 3 (2 scenarios):
        5. uv run pytest tests/agentkit/tape/test_tape.py -v -k "roundtrip" 2>&1 | tee .sisyphus/evidence/final-qa/task-3-roundtrip.txt
        6. (Task 3 scenario 2 is same roundtrip assertion — covered by step 5 output)
      Task 4 (2 scenarios):
        7. uv run pytest tests/coding_agent/plugins/test_summarizer.py -v -k "find_last_finalized" 2>&1 | tee .sisyphus/evidence/final-qa/task-4-fold-boundary.txt
        8. grep -n "anchor_type" src/coding_agent/plugins/summarizer.py src/coding_agent/plugins/topic.py 2>&1 | tee .sisyphus/evidence/final-qa/task-4-no-anchor-type.txt; test $? -eq 1
      Task 5 (2 scenarios):
        9. uv run pytest tests/agentkit/tape/test_tape.py -v -k "backward_compat" 2>&1 | tee .sisyphus/evidence/final-qa/task-5-backward-compat.txt
        10. grep -n 'is_handoff' src/agentkit/tape/tape.py 2>&1 | tee .sisyphus/evidence/final-qa/task-5-is-handoff-detection.txt
      Task 6 (2 scenarios):
        11. grep -rn "anchor_type" tests/ 2>&1 | tee .sisyphus/evidence/final-qa/task-6-anchor-type-compat-only.txt; (assert matches only in backward-compat test functions)
        12. uv run pytest tests/ -v --tb=short 2>&1 | tail -20 | tee .sisyphus/evidence/final-qa/task-6-full-suite.txt
      Task 7 (2 scenarios):
        13. uv run pytest tests/coding_agent/plugins/test_memory.py -v -k "directive" 2>&1 | tee .sisyphus/evidence/final-qa/task-7-memory-directive.txt
        14. grep -A 30 "def on_turn_end" src/coding_agent/plugins/memory.py | grep "self._memories.append" 2>&1 | tee .sisyphus/evidence/final-qa/task-7-no-double-write.txt; test $? -eq 1
      Task 8 (2 scenarios):
        15. grep -n "memory_handler" src/coding_agent/__main__.py 2>&1 | tee .sisyphus/evidence/final-qa/task-8-handler-wired.txt
        16. uv run pytest tests/ -v -k "memory_handler or directive_wiring" 2>&1 | tee .sisyphus/evidence/final-qa/task-8-e2e-directive.txt
      Task 9 (2 scenarios):
        17. uv run python -c "from agentkit.tracing import configure_tracing, get_tracer; configure_tracing(enabled=True); log = get_tracer('test'); log.info('hello', key='value')" 2>&1 | tee .sisyphus/evidence/final-qa/task-9-tracing-config.txt
        18. uv run python -c "from agentkit.tracing import configure_tracing, get_tracer; configure_tracing(enabled=False); log = get_tracer('test'); log.info('should not appear')" 2>&1 | tee .sisyphus/evidence/final-qa/task-9-tracing-disabled.txt
      Task 10 (2 scenarios):
        19. uv run pytest tests/agentkit/test_tracing.py -v -k "pipeline_stage" 2>&1 | tee .sisyphus/evidence/final-qa/task-10-pipeline-tracing.txt
        20. uv run pytest tests/agentkit/runtime/test_pipeline.py -v --tb=short 2>&1 | tee .sisyphus/evidence/final-qa/task-10-no-trace-noise.txt; grep -c "structlog\|stage_start\|stage_end\|directive_execute" .sisyphus/evidence/final-qa/task-10-no-trace-noise.txt | tee -a .sisyphus/evidence/final-qa/task-10-no-trace-noise.txt; (assert count is 0)
      Task 11 (2 scenarios):
        21. uv run pytest tests/agentkit/test_tracing.py -v -k "directive" 2>&1 | tee .sisyphus/evidence/final-qa/task-11-directive-tracing.txt
        22. uv run pytest tests/agentkit/test_tracing.py -v -k "reject" 2>&1 | tee .sisyphus/evidence/final-qa/task-11-reject-reason.txt
    Expected Result: All 22 commands exit successfully (exit code 0 for pytest/python, exit code 1 for negative grep assertions)
    Failure Indicators: Any command returns unexpected exit code
    Evidence: .sisyphus/evidence/final-qa/ (22 individual files) + .sisyphus/evidence/f3-all-scenarios.txt (aggregated summary)

  Scenario: Clean state verification
    Tool: Bash (git + uv run pytest)
    Steps:
      1. Run: git status --porcelain
      2. Assert: output is empty (no uncommitted changes)
      3. Run: uv run pytest tests/ -v --tb=short
      4. Assert: exit code 0, all tests pass
    Expected Result: Working tree is clean AND all tests pass — no local hacks needed
    Failure Indicators: Uncommitted changes present, or tests fail without local modifications
    Evidence: .sisyphus/evidence/f3-clean-state.txt
  ```

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec. Check "Must NOT do" compliance. Detect cross-task contamination.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | VERDICT`

  **QA Scenarios (MANDATORY)**:

  ```
  Scenario: Each commit matches its phase scope
    Tool: Bash (git)
    Steps:
      1. Run: git log --oneline -4 (get 4 phase commits)
      2. For each commit: git diff-tree --no-commit-id --name-only -r <hash>
      3. Assert Phase 1 commit only touches: tape.py, pipeline.py, test_tape.py, test_pipeline.py
      4. Assert Phase 2 commit only touches: tape.py, summarizer.py, topic.py, test_tape.py, test_summarizer.py, test_pipeline.py
      5. Assert Phase 3 commit only touches: __main__.py, memory.py, test_memory.py (or new test file)
      6. Assert Phase 4 commit only touches: new tracing.py, pipeline.py, executor.py, test_tracing.py
    Expected Result: Each commit's file list matches the plan's commit strategy
    Failure Indicators: Unexpected files in a commit, cross-phase contamination
    Evidence: .sisyphus/evidence/f4-commit-scope.txt

  Scenario: No "Must NOT do" violations
    Tool: Bash (grep + git)
    Steps:
      1. Verify builder.py unchanged: git diff HEAD~4 -- src/agentkit/context/builder.py | wc -l == 0
      2. Verify no logging migration: grep -rn "structlog" src/ | grep -v tracing.py | grep -v pipeline.py | grep -v executor.py (should be 0 non-plan files)
      3. Verify no Checkpoint handler: grep -n "checkpoint_handler" src/coding_agent/__main__.py (should be 0)
      4. Verify no disk persistence: grep -rn "open.*write\|sqlite\|lancedb" src/coding_agent/plugins/memory.py (should be 0)
    Expected Result: All "Must NOT" constraints respected
    Failure Indicators: Any forbidden pattern found
    Evidence: .sisyphus/evidence/f4-must-not-violations.txt

  Scenario: No scope creep — diff size proportional to task
    Tool: Bash (git)
    Steps:
      1. Run: git diff --stat HEAD~4 HEAD
      2. Assert: total insertions reasonable (<500 lines for all 4 phases)
      3. Flag any single file with >100 lines changed (unexpected for this plan)
    Expected Result: Changes proportional to plan scope
    Failure Indicators: Unexpectedly large diffs suggesting scope creep
    Evidence: .sisyphus/evidence/f4-scope-creep.txt
  ```

---

## Commit Strategy

| Phase | Message | Files | Pre-commit |
|-------|---------|-------|------------|
| 1 | `fix(tape): handoff() accepts window_start to preserve recent entries` | tape.py, pipeline.py, test_tape.py | `uv run pytest tests/agentkit/tape/ -v` |
| 2 | `refactor(meta): replace anchor_type with semantic meta fields` | tape.py, summarizer.py, topic.py, test_tape.py, test_summarizer.py, test_pipeline.py | `uv run pytest tests/ -v` |
| 3 | `feat(directive): wire MemoryRecord handler, eliminate double-write` | __main__.py, memory.py, test_memory.py or new test file | `uv run pytest tests/ -v` |
| 4 | `feat(tracing): add structlog directive tracing for pipeline and executor` | new tracing.py, pipeline.py, executor.py, new test_tracing.py | `uv run pytest tests/ -v` |

---

## Success Criteria

### Verification Commands
```bash
uv run pytest tests/ -v --tb=short  # Expected: ALL PASS, 0 failures
uv run mypy src/agentkit/tape/tape.py src/agentkit/runtime/pipeline.py src/coding_agent/plugins/summarizer.py src/coding_agent/plugins/topic.py src/coding_agent/plugins/memory.py  # Expected: Success
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass (baseline 841 + new tests)
- [ ] 4 atomic commits, each with green test suite
