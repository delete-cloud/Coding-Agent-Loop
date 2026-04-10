# Review-Fixes Closure Re-Check Design

## Goal

Determine whether the `review-fixes` bucket can be closed as-is or requires one minimal follow-up change before closure.

This spec does **not** define a broad closure-first roadmap. It covers only the next bucket:

- `review-fixes closure re-check`

The target outcome is a grounded verdict:

1. **direct closure** — implementation is already correct; only closure artifacts were missing, or
2. **minimal-tail-then-closure** — one small remaining issue must be fixed before closure.

---

## Context

The current salvage sequence is already narrowed:

- P4: closed
- P2: closed
- next bucket: `review-fixes closure re-check`

This bucket is a strong closure candidate because:

- `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md` shows implementation steps checked off
- `.sisyphus/notepads/review-fixes/issues.md` still records a concrete unresolved-looking tail
- current code still contains `/approve` legacy compatibility behavior in both the HTTP boundary and `SessionManager.submit_approval()`
- tests still explicitly describe part of the current `/approve` behavior as `legacy check`

This means the bucket is **not** safely closable by documentation alone. It requires a focused re-check against runtime behavior and plan intent.

---

## Scope

### In scope

- Re-verify the `review-fixes` bucket against current code, tests, and plan intent
- Determine whether `/approve` legacy behavior is still required by the current design
- Decide whether the bucket is closure-ready now or needs one small follow-up patch
- Produce closure artifacts if the bucket closes

### Out of scope

- Writing a broader closure-first program spec
- Starting the next closure bucket after `review-fixes`
- Starting any new feature bucket such as KB Phase 1
- Opportunistic refactors unrelated to approval lifecycle closure

---

## Design Principle

### Primary decision rule for `/approve` legacy behavior

Default to **remove/tighten-first**, not preserve-first.

Interpret the current legacy compatibility path as **review residual debt** unless the re-check finds explicit evidence that it remains a required contract.

The desired convergence is:

- `ApprovalStore` is the single source of truth for pending approval lifecycle
- session-level `pending_approval` is not allowed to create a fake success path after the store has lost the request
- the HTTP approval endpoint does not continue to depend on stale session-only state unless that dependency is intentionally preserved and justified

### Explicit preserve standard

Preserve legacy behavior only if at least one of the following is proven during the re-check:

1. plan or documentation explicitly requires it as a supported contract
2. tests clearly assert it as intended product behavior rather than historical compatibility
3. the real runtime approval lifecycle still depends on it for correctness

If those conditions are not met, treat the legacy path as removable or tighten-able closure debt.

---

## Execution Design

### 1. Focused verification cluster

This verification is not a single blanket “run related tests” step. It must be split into three layers so the final verdict can distinguish product dependence from historical leftovers.

#### Layer A — Session-manager approval semantics

Validate `submit_approval()` and related session-manager approval state handling directly.

Questions this layer must answer:

- Can approval succeed only because `session.pending_approval` matches while `ApprovalStore` no longer knows the request?
- Does `submit_approval()` still encode legacy success semantics that contradict the plan’s single-source-of-truth direction?
- Are session state and store state kept aligned after approval success, mismatch, timeout, or cleanup?

#### Layer B — HTTP `/sessions/{id}/approve` boundary behavior

Validate the HTTP endpoint independently from direct `SessionManager` calls.

Questions this layer must answer:

- Does the HTTP gate still rely on session-only pending state before consulting store-backed truth?
- Are the `no pending` and `request mismatch` responses driven by intended runtime behavior or only by legacy gate logic?
- Do current HTTP tests still prove desired behavior, or only preserve historical compatibility?

#### Layer C — Real runtime approval lifecycle

Validate the real approval lifecycle path, not just isolated helpers.

Questions this layer must answer:

- Does the runtime path from approval request emission to response consumption still depend on the legacy path?
- After timeout / completion / cleanup, do session state and `ApprovalStore` state converge cleanly?
- Is the unresolved note in `.sisyphus/notepads/review-fixes/issues.md` still reproducing as a real runtime dependency?

### 2. Plan and issues gap check

Run a line-by-line closure check against:

- `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md`
- `.sisyphus/notepads/review-fixes/issues.md`
- related approval tests and current implementation

The re-check must answer:

- whether Task 3’s acceptance criteria are fully met in the current codebase
- whether the remaining note is outdated closure drift or a still-real defect
- whether the plan’s explicit instruction to remove stale `pending_approval` fallback semantics has truly been honored

### 3. `/approve` legacy path disposition decision

Make an explicit decision, not an implicit drift-based one.

Possible outcomes:

- **Preserve** — only if explicit contract evidence is found
- **Tighten** — keep limited compatibility surface but remove store-bypassing success semantics
- **Remove** — if no valid contract remains and tests/runtime confirm it is debt only

Default expectation for this re-check is **tighten or remove**, not preserve.

### 4. Closure artifact requirements

If the bucket is closure-ready, produce all of the following:

- fresh evidence file under `.sisyphus/evidence/`
- updated `.sisyphus/notepads/review-fixes/` notes
- plan closure note or equivalent plan-state update
- final verdict recorded as either:
  - `direct closure`, or
  - `minimal-tail-then-closure`

If the bucket is **not** closure-ready, do not broaden scope. Record exactly one smallest remaining tail that blocks closure.

---

## Acceptance Criteria

This spec is satisfied only if the re-check yields a concrete, evidence-backed bucket verdict.

### Direct closure

Allowed only if all of the following are true:

- focused verification cluster is green across session-manager, HTTP boundary, and runtime lifecycle layers
- no runtime path still depends on legacy session-only approval success behavior
- the unresolved note in `review-fixes/issues.md` is shown to be stale or already addressed
- closure artifacts are written

### Minimal-tail-then-closure

Required if any of the following is true:

- `submit_approval()` still succeeds via session-only fallback after the store no longer knows the request
- the HTTP approval endpoint still gates primarily on legacy session-only state in a way that contradicts current plan intent
- runtime approval lifecycle still relies on legacy semantics for correctness
- the note in `review-fixes/issues.md` still corresponds to real behavior

In that case, the follow-up work must be narrowed to one smallest blocking tail, implemented, re-verified, and then closed.

---

## Risks and Non-Goals

### Main risk

The biggest risk is misclassifying legacy approval behavior as a protected contract when the plan actually intended to collapse truth into `ApprovalStore`.

That would preserve complexity in two layers:

- session state
- store state

and keep the bucket “green-looking” without actually resolving the review finding.

### Non-goal

This re-check is not a redesign of the whole approval system. It is only a closure decision and, at most, one minimal tail fix.

---

## Recommended Next Step

Execute `review-fixes closure re-check` exactly in this order:

1. focused verification cluster (3 layers)
2. plan + `issues.md` gap check
3. `/approve` legacy-path disposition decision
4. either close immediately or implement one minimal tail and then close
