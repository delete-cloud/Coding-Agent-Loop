# Anchor-Type-System + Tape-View Sequential Closure Re-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether `anchor-type-system` and, if eligible, `tape-view` are closure-ready buckets, and either close them or stop cleanly when they no longer qualify as closure work.

**Architecture:** Execute two sequential closure re-check buckets with explicit dependency gating. First verify `anchor-type-system` across core type, tape/model compatibility, and consumer/plugin migration. Only if anchor receives a closure verdict (`direct closure` or `minimal-tail-then-closure`) proceed to `tape-view`, verifying TapeView core, ContextBuilder integration, and Pipeline integration. Treat plan/code/tests comparison as evidence review, not backlog completion.

**Tech Stack:** Python 3.14, pytest, mypy/LSP diagnostics, agentkit tape/context/runtime modules, coding_agent summarizer/topic plugins

**Scope:** Only sequential closure re-check for `anchor-type-system` and `tape-view`, including verification, gap analysis, at most one minimal semantic-tail fix per bucket, and closure artifacts for buckets that actually close.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `docs/superpowers/plans/2026-04-02-anchor-type-system.md` | Read/Update | Source plan for anchor closure verification and later closure note |
| `docs/superpowers/plans/2026-04-02-tape-view.md` | Read/Update | Source plan for tape-view closure verification and later closure note |
| `.sisyphus/notepads/2026-04-02-anchor-type-system/` | Create/Update | Anchor closure notes if anchor closes |
| `.sisyphus/notepads/2026-04-02-tape-view/` | Create/Update | Tape-view closure notes if tape-view closes |
| `.sisyphus/evidence/anchor-type-system-closure-2026-04-10.txt` | Create | Fresh verification and final anchor verdict |
| `.sisyphus/evidence/tape-view-closure-2026-04-10.txt` | Create | Fresh verification and final tape-view verdict |
| `src/agentkit/tape/anchor.py` | Verify / maybe modify | Anchor structured type and serialization |
| `tests/agentkit/tape/test_anchor.py` | Verify / maybe modify | Anchor type and serialization tests |
| `src/agentkit/tape/models.py` | Verify / maybe modify | Entry polymorphic dispatch and anchor compatibility |
| `src/agentkit/tape/tape.py` | Verify / maybe modify | Tape loading and anchor-aware window semantics |
| `tests/agentkit/tape/test_models.py` | Verify / maybe modify | Model compatibility tests |
| `tests/agentkit/tape/test_tape.py` | Verify / maybe modify | Tape loading/window tests |
| `src/agentkit/context/builder.py` | Verify / maybe modify | Anchor-aware consumer handling and TapeView support |
| `tests/agentkit/context/test_builder.py` | Verify / maybe modify | Builder integration coverage |
| `src/coding_agent/plugins/summarizer.py` | Verify / maybe modify | Summarizer migration to typed anchors |
| `src/coding_agent/plugins/topic.py` | Verify / maybe modify | Topic migration to typed anchors |
| `tests/coding_agent/plugins/test_summarizer.py` | Verify / maybe modify | Summarizer anchor tests |
| `tests/coding_agent/plugins/test_topic.py` | Verify / maybe modify | Topic anchor tests |
| `src/agentkit/tape/view.py` | Verify / maybe modify | TapeView core abstraction |
| `tests/agentkit/tape/test_view.py` | Verify / maybe modify | TapeView core tests |
| `src/agentkit/runtime/pipeline.py` | Verify / maybe modify | Pipeline use of TapeView.from_tape |
| `tests/agentkit/runtime/test_pipeline.py` | Verify / maybe modify | Pipeline TapeView integration tests |

---

## Global Execution Rules

- [ ] **Rule 1: Treat plan/code/tests comparison as evidence review, not backlog completion**

This plan does **not** authorize implementing every old bullet from the historical implementation plans. Historical steps are evidence sources used to test closure claims against the current codebase.

- [ ] **Rule 2: Allow at most one minimal semantic-tail fix per bucket**

If a bucket has more than one independent semantic gap, re-opens a prerequisite dependency, or requires broader redesign, it must be classified as `not a closure bucket` immediately.

- [ ] **Rule 3: Tape-view is gated on proven anchor invariants**

Do not enter the tape-view bucket unless anchor closure verification has already proven:

- typed anchor dispatch
- anchor-aware tape/window behavior
- consumer-visible anchor handling

---

## Part I — Anchor-Type-System Closure Re-Check

### Task 1: Run anchor focused verification cluster

**Files:**
- Verify: `src/agentkit/tape/anchor.py`
- Verify: `tests/agentkit/tape/test_anchor.py`
- Verify: `src/agentkit/tape/models.py`
- Verify: `src/agentkit/tape/tape.py`
- Verify: `tests/agentkit/tape/test_models.py`
- Verify: `tests/agentkit/tape/test_tape.py`
- Verify: `src/agentkit/context/builder.py`
- Verify: `src/coding_agent/plugins/summarizer.py`
- Verify: `src/coding_agent/plugins/topic.py`
- Verify: `tests/agentkit/context/test_builder.py`
- Verify: `tests/coding_agent/plugins/test_summarizer.py`
- Verify: `tests/coding_agent/plugins/test_topic.py`

**Goal:** Gather fresh verification evidence across the three anchor layers without changing behavior first.

- [ ] **Step 1: Run core anchor type and serialization tests**

Run:

```bash
uv run pytest tests/agentkit/tape/test_anchor.py -v
```

Expected:
- PASS if structured `Anchor` behaviors are present and stable
- output proves `anchor_type`, `source_ids`, and serialization behaviors are still covered

- [ ] **Step 2: Run tape/model compatibility tests**

Run:

```bash
uv run pytest tests/agentkit/tape/test_models.py tests/agentkit/tape/test_tape.py -v
```

Expected:
- PASS if polymorphic dispatch and anchor-aware tape behavior still work
- output reveals whether compatibility is still real or only accidental

- [ ] **Step 3: Run consumer/plugin migration tests**

Run:

```bash
uv run pytest tests/agentkit/context/test_builder.py tests/coding_agent/plugins/test_summarizer.py tests/coding_agent/plugins/test_topic.py -v
```

Expected:
- PASS if consumer-facing anchor semantics are actually exercised
- output confirms summarizer/topic migration is real, not dead code

- [ ] **Step 4: Record a three-layer anchor findings summary**

Summarize:

- Layer A: core Anchor type/serialization state
- Layer B: tape/model compatibility state
- Layer C: builder/plugin migration state

Expected: one concise anchor findings report ready for gap check.

---

### Task 2: Run anchor plan/code/tests gap check and decide anchor verdict

**Files:**
- Verify: `docs/superpowers/plans/2026-04-02-anchor-type-system.md`
- Verify: `src/agentkit/tape/anchor.py`
- Verify: `src/agentkit/tape/models.py`
- Verify: `src/agentkit/tape/tape.py`
- Verify: `src/agentkit/context/builder.py`
- Verify: `src/coding_agent/plugins/summarizer.py`
- Verify: `src/coding_agent/plugins/topic.py`
- Verify: `tests/agentkit/tape/test_anchor.py`
- Verify: `tests/agentkit/tape/test_models.py`
- Verify: `tests/agentkit/tape/test_tape.py`
- Verify: `tests/agentkit/context/test_builder.py`
- Verify: `tests/coding_agent/plugins/test_summarizer.py`
- Verify: `tests/coding_agent/plugins/test_topic.py`

**Goal:** Determine whether anchor is closure-ready, has one minimal semantic tail, or is no longer a closure bucket.

- [ ] **Step 1: Re-read the anchor plan as evidence, not as backlog**

Read `docs/superpowers/plans/2026-04-02-anchor-type-system.md` and extract a checklist of closure claims:

- structured Anchor type exists
- polymorphic deserialization works
- tape loading honors anchor semantics
- consumer/plugin migration is real
- backward compatibility remains intact

Expected: one closure checklist grounded in current behavior claims.

- [ ] **Step 2: Compare the current code and tests against the checklist**

Expected:
- identify whether each closure claim is proven by current code and tests
- separate “implementation present” from “behavior actually proven”

- [ ] **Step 3: Decide the anchor verdict**

Write exactly one of:

- `direct closure`
- `minimal-tail-then-closure`
- `not a closure bucket`

Expected: a narrow verdict with justification. If `minimal-tail-then-closure`, identify exactly one bounded semantic gap.

---

### Task 3: If needed, fix one minimal anchor semantic tail only

**Files:**
- Modify only if Task 2 produced `minimal-tail-then-closure`
- Likely modify one or more of:
  - `src/agentkit/tape/anchor.py`
  - `src/agentkit/tape/models.py`
  - `src/agentkit/tape/tape.py`
  - `src/agentkit/context/builder.py`
  - `src/coding_agent/plugins/summarizer.py`
  - `src/coding_agent/plugins/topic.py`
  - their direct tests

**Goal:** Fix one bounded semantic gap only. If more than one independent gap exists, stop and reclassify anchor as `not a closure bucket`.

- [ ] **Step 1: Write one failing regression test for the anchor tail**

Only if Task 2 found `minimal-tail-then-closure`.

Expected: one targeted failing test for the exact semantic gap.

- [ ] **Step 2: Run the targeted test and verify it fails first**

Run the exact pytest selector for the new regression.

Expected: FAIL for the expected reason.

- [ ] **Step 3: Implement the minimal anchor fix**

Expected:
- smallest code delta that closes the specific semantic gap
- no broader anchor redesign

- [ ] **Step 4: Re-run the targeted test and the affected anchor layer tests**

Expected: PASS.

- [ ] **Step 5: Re-state the anchor verdict**

Expected: anchor moves from `minimal-tail-then-closure` to closure-ready.

---

### Task 4: Close anchor or stop cleanly

**Files:**
- Create: `.sisyphus/evidence/anchor-type-system-closure-2026-04-10.txt`
- Create or modify: `.sisyphus/notepads/2026-04-02-anchor-type-system/decisions.md`
- Create or modify: `.sisyphus/notepads/2026-04-02-anchor-type-system/issues.md`
- Create or modify: `.sisyphus/notepads/2026-04-02-anchor-type-system/learnings.md`
- Modify: `docs/superpowers/plans/2026-04-02-anchor-type-system.md`

**Goal:** Either close anchor formally or record the stop verdict that prevents tape-view from starting.

- [ ] **Step 1: Run fresh anchor closure verification**

Run:

```bash
uv run pytest tests/agentkit/tape/test_anchor.py tests/agentkit/tape/test_models.py tests/agentkit/tape/test_tape.py tests/agentkit/context/test_builder.py tests/coding_agent/plugins/test_summarizer.py tests/coding_agent/plugins/test_topic.py -v
```

Expected: all anchor-relevant tests PASS.

- [ ] **Step 2: Run diagnostics on touched anchor source files**

Check touched source files with diagnostics.

Expected: no LSP errors on changed source files.

- [ ] **Step 3: Write anchor evidence and notes if anchor closes**

If anchor verdict is `direct closure` or `minimal-tail-then-closure`, record:

- verification commands/results
- whether Task 3 was skipped or executed
- final anchor verdict
- proven invariants relevant to tape-view entry

- [ ] **Step 4: If anchor is `not a closure bucket`, stop here**

Expected:
- record the stop reason
- do not proceed to tape-view

---

## Gate — Tape-View May Start Only If Anchor Closed

- [ ] **Gate check: confirm anchor verdict is closure, not stop**

Tape-view may proceed only if:

- anchor verdict is `direct closure` or `minimal-tail-then-closure`
- anchor closure evidence proves:
  - typed anchor dispatch
  - anchor-aware tape/window behavior
  - consumer-visible anchor handling

If any gate item is missing, tape-view does not start and the plan stops successfully at anchor.

---

## Part II — Tape-View Closure Re-Check

### Task 5: Run tape-view focused verification cluster

**Files:**
- Verify: `src/agentkit/tape/view.py`
- Verify: `tests/agentkit/tape/test_view.py`
- Verify: `src/agentkit/context/builder.py`
- Verify: `tests/agentkit/context/test_builder.py`
- Verify: `src/agentkit/runtime/pipeline.py`
- Verify: `tests/agentkit/runtime/test_pipeline.py`

**Goal:** Gather fresh verification evidence across the three tape-view layers.

- [ ] **Step 1: Run TapeView core tests**

Run:

```bash
uv run pytest tests/agentkit/tape/test_view.py -v
```

Expected: PASS if TapeView core behavior is stable.

- [ ] **Step 2: Run ContextBuilder integration tests**

Run:

```bash
uv run pytest tests/agentkit/context/test_builder.py -k "view or TapeView" -v
```

Expected: PASS if builder truly accepts TapeView.

- [ ] **Step 3: Run Pipeline integration tests**

Run:

```bash
uv run pytest tests/agentkit/runtime/test_pipeline.py -k "view or TapeView" -v
```

Expected: PASS if pipeline actually uses TapeView as claimed.

- [ ] **Step 4: Record a three-layer tape-view findings summary**

Summarize:

- Layer A: TapeView core
- Layer B: ContextBuilder integration
- Layer C: Pipeline integration

Expected: one concise tape-view findings report ready for gap check.

---

### Task 6: Run tape-view plan/code/tests gap check and decide tape-view verdict

**Files:**
- Verify: `docs/superpowers/plans/2026-04-02-tape-view.md`
- Verify: `src/agentkit/tape/view.py`
- Verify: `src/agentkit/context/builder.py`
- Verify: `src/agentkit/runtime/pipeline.py`
- Verify: `tests/agentkit/tape/test_view.py`
- Verify: `tests/agentkit/context/test_builder.py`
- Verify: `tests/agentkit/runtime/test_pipeline.py`

**Goal:** Determine whether tape-view is closure-ready, has one minimal semantic tail, or is not a closure bucket.

- [ ] **Step 1: Re-read the tape-view plan as evidence**

Extract a checklist:

- TapeView exists and is exported
- builder accepts TapeView
- pipeline uses TapeView.from_tape
- backward compatibility with raw Tape remains

Expected: one tape-view closure checklist.

- [ ] **Step 2: Compare current code/tests against the checklist**

Expected:
- distinguish present code from proven behavior
- confirm that anchor prerequisites are actually satisfied in current behavior, not only assumed

- [ ] **Step 3: Decide the tape-view verdict**

Write exactly one of:

- `direct closure`
- `minimal-tail-then-closure`
- `not a closure bucket`

Expected: narrow verdict with justification. If `minimal-tail-then-closure`, identify exactly one bounded semantic gap.

---

### Task 7: If needed, fix one minimal tape-view semantic tail only

**Files:**
- Modify only if Task 6 produced `minimal-tail-then-closure`
- Likely modify one or more of:
  - `src/agentkit/tape/view.py`
  - `src/agentkit/context/builder.py`
  - `src/agentkit/runtime/pipeline.py`
  - direct tests

**Goal:** Fix one bounded semantic gap only. If a second independent gap appears, tape-view becomes `not a closure bucket` immediately.

- [ ] **Step 1: Write one failing regression test for the tape-view tail**

Expected: one targeted failing test for the exact semantic gap.

- [ ] **Step 2: Run the targeted test and verify it fails first**

Expected: FAIL for the expected reason.

- [ ] **Step 3: Implement the minimal tape-view fix**

Expected: smallest code delta with no broader tape architecture redesign.

- [ ] **Step 4: Re-run the targeted test and affected tape-view tests**

Expected: PASS.

- [ ] **Step 5: Re-state the tape-view verdict**

Expected: tape-view moves from `minimal-tail-then-closure` to closure-ready.

---

### Task 8: Close tape-view or stop cleanly

**Files:**
- Create: `.sisyphus/evidence/tape-view-closure-2026-04-10.txt`
- Create or modify: `.sisyphus/notepads/2026-04-02-tape-view/decisions.md`
- Create or modify: `.sisyphus/notepads/2026-04-02-tape-view/issues.md`
- Create or modify: `.sisyphus/notepads/2026-04-02-tape-view/learnings.md`
- Modify: `docs/superpowers/plans/2026-04-02-tape-view.md`

**Goal:** Close tape-view formally or record a clean stop verdict.

- [ ] **Step 1: Run fresh tape-view closure verification**

Run:

```bash
uv run pytest tests/agentkit/tape/test_view.py tests/agentkit/context/test_builder.py tests/agentkit/runtime/test_pipeline.py -v
```

Expected: all tape-view-relevant tests PASS.

- [ ] **Step 2: Run diagnostics on touched tape-view source files**

Check touched source files with diagnostics.

Expected: no LSP errors on changed source files.

- [ ] **Step 3: Write tape-view evidence and notes if tape-view closes**

Record:

- verification commands/results
- whether Task 7 was skipped or executed
- final tape-view verdict

- [ ] **Step 4: If tape-view is `not a closure bucket`, stop here**

Expected:
- record the stop reason cleanly
- do not broaden into new implementation within this plan

---

## Summary

| Task | Purpose | Outcome |
|------|---------|---------|
| 1 | Anchor focused verification | Fresh three-layer anchor evidence |
| 2 | Anchor gap check + verdict | Decide direct/minimal-tail/not-closure for anchor |
| 3 | Conditional anchor tail fix | Fix one bounded semantic anchor gap if needed |
| 4 | Anchor closure or stop | Close anchor or stop before tape-view |
| Gate | Dependency gate | Tape-view only starts if anchor closure proves required invariants |
| 5 | Tape-view focused verification | Fresh three-layer tape-view evidence |
| 6 | Tape-view gap check + verdict | Decide direct/minimal-tail/not-closure for tape-view |
| 7 | Conditional tape-view tail fix | Fix one bounded semantic tape-view gap if needed |
| 8 | Tape-view closure or stop | Close tape-view or stop cleanly |

## Success Criteria

- anchor receives a grounded closure verdict
- tape-view proceeds only if anchor closure proves the invariants it depends on
- each bucket allows at most one bounded semantic-tail fix before forced reclassification
- plan/code/tests comparison is used as evidence review, not as license to re-implement historical backlog
- final output is two independent bucket verdicts, not a merged result
