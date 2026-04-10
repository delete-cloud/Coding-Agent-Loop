# Review-Fixes Closure Re-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether the `review-fixes` bucket is closure-ready as-is or needs one minimal follow-up fix before closure, with explicit focus on the `/approve` legacy path.

**Architecture:** Treat this as a closure-verification bucket, not a new feature build. Re-check the approval path at three layers — `submit_approval()` semantics, HTTP `/sessions/{id}/approve` boundary behavior, and the real runtime approval lifecycle — while keeping `ApprovalStore` as the intended approval truth source and treating `session.pending_approval` as a transient runtime/UI projection unless explicit evidence proves otherwise.

**Tech Stack:** Python 3.14, FastAPI, asyncio, pytest, httpx/httpx-sse, existing `ApprovalStore`, existing HTTP session manager

**Scope:** Only `review-fixes closure re-check`, including verification, gap analysis, possible minimal approval-tail fix, and closure artifacts. No broader approval-system redesign and no next-bucket work.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md` | Read/Update | Source plan to verify against and later annotate with closure outcome |
| `.sisyphus/notepads/review-fixes/issues.md` | Read/Update | Current unresolved tail source; must be resolved or reclassified during closure |
| `.sisyphus/notepads/review-fixes/decisions.md` | Update | Record the legacy-path disposition decision and closure rationale |
| `.sisyphus/notepads/review-fixes/learnings.md` | Create or update | Record fresh verification results and closure findings if needed |
| `.sisyphus/evidence/review-fixes-closure-2026-04-10.txt` | Create | Capture fresh verification commands, results, and final verdict |
| `src/coding_agent/ui/session_manager.py` | Verify / maybe modify | Check whether `submit_approval()` still allows store-bypassing success via legacy fallback |
| `src/coding_agent/ui/http_server.py` | Verify / maybe modify | Check whether `/sessions/{id}/approve` still gates primarily on legacy session-only state |
| `tests/ui/test_session_manager_public_api.py` | Verify / maybe modify | Session-manager layer evidence for approval lifecycle correctness |
| `tests/ui/test_http_server.py` | Verify / maybe modify | HTTP-boundary and legacy-path evidence |
| `tests/approval/test_store.py` | Verify | Confirms `ApprovalStore` behavior remains the approval lifecycle truth source |

---

## Task 1: Reproduce the approval path at three verification layers

**Files:**
- Verify: `src/coding_agent/ui/session_manager.py:467-524`
- Verify: `src/coding_agent/ui/http_server.py:486-534`
- Verify: `tests/ui/test_session_manager_public_api.py`
- Verify: `tests/ui/test_http_server.py`
- Verify: `tests/approval/test_store.py`

**Goal:** Gather fresh evidence for the three-layer approval re-check without changing behavior yet.

- [ ] **Step 1: Run the session-manager layer approval checks**

Run:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -k "runtime_timeout or submit_approval" -v
```

Expected:
- PASS if `submit_approval()` and timeout cleanup match current tests
- Output identifies whether current tests still treat legacy fallback as acceptable behavior

- [ ] **Step 2: Run the HTTP approval boundary checks**

Run:

```bash
uv run pytest tests/ui/test_http_server.py -k "approve" -v
```

Expected:
- PASS or targeted failures that reveal whether `/sessions/{id}/approve` still depends on legacy session-only gate behavior
- Output includes the `legacy check` tests that must be re-evaluated during gap check

- [ ] **Step 3: Run the real runtime approval lifecycle checks**

Run:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -k "runtime_timeout" -v
uv run pytest tests/approval/test_store.py -k "remove_request or wait_for_response or respond" -v
```

Expected:
- PASS if runtime timeout cleanup still converges session and store state
- Evidence for whether runtime correctness depends on legacy fallback or on `ApprovalStore`

- [ ] **Step 4: Record the three-layer findings before any edits**

Summarize in working notes:

- Session-manager layer: does `submit_approval()` still allow store-bypassing success?
- HTTP layer: does the endpoint still gate primarily on `session.pending_approval`?
- Runtime layer: does approval lifecycle correctness still depend on legacy semantics?

Expected: one concise three-layer verdict ready for plan/issue comparison.

---

## Task 2: Run a line-by-line gap check against the review-fixes plan and open issue

**Files:**
- Verify: `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md:300-495`
- Verify: `.sisyphus/notepads/review-fixes/issues.md`
- Verify: `src/coding_agent/ui/session_manager.py:467-524`
- Verify: `src/coding_agent/ui/http_server.py:486-534`
- Verify: `tests/ui/test_http_server.py:387-492`

**Goal:** Determine whether the remaining `issues.md` note is stale closure drift or a live gap against the original plan intent.

- [ ] **Step 1: Re-read the Task 3 acceptance language in the original plan**

Read and compare these statements from `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md`:

- cleanup must keep session state and store state aligned
- `submit_approval()` fallback logic treating stale `pending_approval` as success should be removed

Expected: a precise checklist of approval-specific acceptance criteria.

- [ ] **Step 2: Compare the current code against that checklist**

Check in `src/coding_agent/ui/session_manager.py` whether the following still exists:

```python
legacy_pending_matches = (
    session.pending_approval is not None
    and session.pending_approval.get("request_id") == request_id
)

if not success and legacy_pending_matches:
    success = True
```

Expected:
- If present, mark it as a live mismatch against the original Task 3 intent.
- If absent, treat the issue note as likely stale and continue to HTTP/runtime confirmation.

- [ ] **Step 3: Compare the HTTP endpoint gate against the intended truth-source model**

Inspect whether `src/coding_agent/ui/http_server.py` still relies on:

```python
if not session_manager.has_approval_request(session_id):
    raise HTTPException(status_code=400, detail="No pending approval request")
if not session_manager.matches_approval_request(session_id, req_id):
    raise HTTPException(status_code=400, detail="Request ID mismatch")
```

Expected:
- Determine whether this is only a UI/projection gate or whether it creates a conflicting second truth source.

- [ ] **Step 4: Compare current tests against plan intent, not just code reality**

Inspect the approval-related tests in `tests/ui/test_http_server.py`, especially those labeled `legacy check`.

Expected:
- classify each test as one of:
  - intended contract
  - historical compatibility shell
  - candidate for tightening/removal if legacy fallback is debt

- [ ] **Step 5: Produce one gap-check verdict**

Write one explicit result:

- `no live gap` — note is stale, closure can proceed, or
- `live minimal gap` — one approval-tail mismatch still exists and must be fixed

Expected: this verdict determines whether Task 3 is needed.

---

## Task 3: If needed, fix the smallest approval tail only

**Files:**
- Modify only if Task 2 finds a live gap
- Likely modify: `src/coding_agent/ui/session_manager.py`
- Maybe modify: `src/coding_agent/ui/http_server.py`
- Maybe modify: `tests/ui/test_session_manager_public_api.py`
- Maybe modify: `tests/ui/test_http_server.py`

**Goal:** If the re-check finds a real mismatch, fix only the smallest approval tail needed to make the bucket closure-ready.

**Important scope rule:** This task is conditional. Skip it entirely if Task 2 concludes the issue is stale and closure can proceed directly.

- [ ] **Step 1: Write the smallest failing regression test for the live gap**

Only do this if Task 2 produced `live minimal gap`.

The test must target one concrete defect such as:

- `submit_approval()` succeeds when `ApprovalStore` no longer knows the request
- HTTP `/approve` accepts/rejects based on legacy session-only state in a way that contradicts the intended truth-source model

Expected: one narrowly-scoped failing test proving the blocking tail is real.

- [ ] **Step 2: Run the new regression test and verify it fails first**

Run the exact targeted pytest command for the new test.

Expected: FAIL for the precise legacy-tail reason identified in Task 2.

- [ ] **Step 3: Implement the minimal fix without redesigning approval state**

Implementation rule:

- keep `ApprovalStore` as the approval truth source
- allow `session.pending_approval` to remain only as transient runtime/UI projection if still useful
- do **not** broaden this into approval-model refactoring

Expected:
- the defect is fixed with the smallest code delta that aligns runtime behavior with plan intent

- [ ] **Step 4: Re-run the targeted regression test and the affected approval checks**

Run:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -k "submit_approval or runtime_timeout" -v
uv run pytest tests/ui/test_http_server.py -k "approve" -v
uv run pytest tests/approval/test_store.py -k "remove_request or wait_for_response or respond" -v
```

Expected: PASS.

- [ ] **Step 5: Re-state the post-fix tail verdict**

Expected: the bucket now moves from `live minimal gap` to `closure-ready`.

---

## Task 4: Run closure verification and produce closure artifacts

**Files:**
- Create: `.sisyphus/evidence/review-fixes-closure-2026-04-10.txt`
- Modify: `.sisyphus/notepads/review-fixes/issues.md`
- Modify: `.sisyphus/notepads/review-fixes/decisions.md`
- Create or modify: `.sisyphus/notepads/review-fixes/learnings.md`
- Modify: `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md`

**Goal:** Close the bucket formally once the re-check verdict is settled.

- [ ] **Step 1: Run the focused closure verification set**

Run:

```bash
uv run pytest tests/approval/test_store.py -v
uv run pytest tests/ui/test_session_manager_public_api.py -v
uv run pytest tests/ui/test_http_server.py -v
```

Expected: all approval- and review-fixes-related tests PASS.

- [ ] **Step 2: Run diagnostics on the touched source files**

Check:

- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/approval/store.py`

Expected: no LSP errors on changed source files.

- [ ] **Step 3: Run fresh full-suite verification**

Run:

```bash
uv run pytest tests/ -v
```

Expected: full suite PASS.

- [ ] **Step 4: Write closure evidence**

Create `.sisyphus/evidence/review-fixes-closure-2026-04-10.txt` and include:

- focused verification commands and results
- full-suite result
- whether Task 3 was skipped or executed
- `/approve` legacy path verdict: preserve / tighten / remove
- final bucket verdict: `direct closure` or `minimal-tail-then-closure`

- [ ] **Step 5: Update notes and plan status**

Update:

- `.sisyphus/notepads/review-fixes/issues.md`
- `.sisyphus/notepads/review-fixes/decisions.md`
- `.sisyphus/notepads/review-fixes/learnings.md` (create if missing)
- `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md`

Expected:
- note whether the old issue was stale or real
- record the final truth-source decision (`ApprovalStore` vs session projection)
- record closure evidence path and final verdict

---

## Summary

| Task | Purpose | Outcome |
|------|---------|---------|
| 1 | Three-layer verification | Establish fresh evidence for session-manager, HTTP, and runtime approval behavior |
| 2 | Plan + issue gap check | Decide whether the open note is stale or a live mismatch |
| 3 | Conditional minimal tail fix | Fix exactly one smallest blocking approval tail if needed |
| 4 | Closure verification + artifacts | Produce final verdict and close the bucket |

## Success Criteria

- `review-fixes` ends with an evidence-backed bucket verdict
- approval truth-source reasoning is explicit: `ApprovalStore` remains the truth source, `session.pending_approval` is not allowed to create a false success path
- if a live tail exists, only one smallest blocking tail is fixed
- closure artifacts are written and the plan is updated with final status
