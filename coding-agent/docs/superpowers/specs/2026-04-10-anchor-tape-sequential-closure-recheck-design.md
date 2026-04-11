# Anchor-Type-System + Tape-View Sequential Closure Re-Check Design

## Goal

Determine, in order, whether the next two likely closure-ready buckets can be closed through closure re-check work rather than treated as new implementation buckets.

This spec covers two sequential buckets:

1. `anchor-type-system closure re-check`
2. `tape-view closure re-check`

It does **not** define a single combined closure verdict. The output of this spec is two independent bucket verdicts, with `tape-view` gated on the outcome of `anchor-type-system`.

The only legal verdicts for each bucket are:

1. **direct closure**
2. **minimal-tail-then-closure**
3. **not a closure bucket**

---

## Context

The current closure-first salvage sequence has already advanced through:

- P4: closed
- P2: closed
- review-fixes: closed

The next likely candidates are `anchor-type-system` and then `tape-view` because:

- both have complete implementation plans under `docs/superpowers/plans/`
- the relevant implementation surfaces already appear in the codebase
- neither plan currently has the same explicit closure note / closure evidence treatment now present on P2, P4, and review-fixes
- `tape-view` explicitly depends on `anchor-type-system`, so the order is not interchangeable

Current repo evidence suggests a split state:

- **implementation surfaces appear present** for both buckets
- **closure artifacts are incomplete or absent**, especially in the matching `.sisyphus/notepads/` directories

That makes these buckets good closure-first candidates, but not buckets that can be closed by assumption. They require focused re-check.

---

## Scope

### In scope

- Re-check `anchor-type-system` against plan, code, tests, and closure artifacts
- If anchor receives a closure verdict, re-check `tape-view` the same way
- Make explicit per-bucket verdicts
- Produce closure artifacts only for buckets that actually close

### Out of scope

- Re-implementing anchor or tape-view from scratch
- Treating anchor and tape-view as a single merged verdict
- Starting the bucket after tape-view
- Broad tape architecture refactoring beyond one minimal tail fix per bucket

---

## Design Principle

### Sequential verdicts, not merged verdicts

This spec intentionally avoids a combined “anchor+tape” outcome.

Why:

- `tape-view` depends on anchor surfaces and semantics
- if anchor turns out not to be closure-ready, any tape-view closure judgement becomes contaminated
- closure work must stop as soon as a bucket no longer looks like re-check + one minimal tail fix

Therefore:

- `anchor-type-system` gets its own verdict first
- `tape-view` is evaluated only after anchor has a closure verdict
- the spec succeeds even if only anchor is resolved and tape-view is deferred by a stop rule

### Hard stop rule

For each bucket, closure re-check ends as soon as one of these is true:

1. **direct closure** — verification and gap check show no live gap
2. **minimal-tail-then-closure** — exactly one smallest blocking semantic gap is fixed, then re-verified
3. **not a closure bucket** — the work no longer looks like minimal closure work

If a bucket reaches `not a closure bucket`, closure-first stops for that bucket immediately.

Additionally:

- if `anchor-type-system` is not a closure bucket, `tape-view` does **not** proceed in this spec
- if `anchor-type-system` closes, `tape-view` may proceed

For avoidance of doubt, a “minimal tail” means **one bounded semantic gap**, not a bundle of loosely related edits across several behaviors. If re-check reveals a second independent gap, a reopened dependency, or any redesign requirement, the bucket must immediately become `not a closure bucket`.

---

## Execution Design

## Part I — Anchor-Type-System Closure Re-Check

### 1. Focused verification cluster

The anchor bucket should be verified across the surfaces its plan explicitly introduced.

At minimum, the re-check must cover:

#### Layer A — Anchor type and serialization

Validate:

- `src/agentkit/tape/anchor.py`
- `tests/agentkit/tape/test_anchor.py`

Questions:

- Is `Anchor` present as a structured type rather than only meta conventions?
- Are `anchor_type`, `source_ids`, and serialization/deserialization behaviors covered and green?

#### Layer B — Tape/model compatibility

Validate:

- `src/agentkit/tape/models.py`
- `src/agentkit/tape/tape.py`
- related tape/model tests

Questions:

- Does `Entry.from_dict()` do polymorphic anchor dispatch?
- Does `Tape.load_jsonl()` honor anchor semantics while preserving old-format compatibility?

#### Layer C — Consumers and plugin migration

Validate:

- `src/agentkit/context/builder.py`
- `src/coding_agent/plugins/summarizer.py`
- `src/coding_agent/plugins/topic.py`
- related tests

Questions:

- Do consumer layers use typed anchor semantics correctly?
- Are summarizer/topic migrations actually reflected in tests?
- Is there any remaining hidden dependence on old-only anchor conventions?

### 2. Plan / code / tests gap check

Run a line-by-line closure comparison against:

- `docs/superpowers/plans/2026-04-02-anchor-type-system.md`
- current implementation files
- current tests
- matching notepad directory under `.sisyphus/notepads/2026-04-02-anchor-type-system/`

The gap check must answer:

- whether the plan’s implementation surfaces are all present
- whether tests actually cover the intended behaviors rather than only legacy compatibility
- whether the bucket only lacks closure artifacts or still has a live behavioral gap

This comparison is **evidence review**, not backlog completion. Historical plan items are used to test closure claims against the current codebase; they do not automatically justify implementing every old bullet if current behavior and dependency claims are already satisfied.

### 3. Anchor verdict standard

#### direct closure

Allowed only if:

- focused verification is green
- plan/code/tests align with no live behavioral gap
- missing work is only closure bookkeeping

#### minimal-tail-then-closure

Allowed only if:

- there is exactly one smallest blocking semantic tail
- the tail is localized
- fixing it does not require redesigning anchor semantics
- after one fix + one full re-verify, the bucket closes

#### not a closure bucket

Required if:

- multiple independent gaps remain
- anchor semantics still require significant implementation rather than re-check
- fixing the bucket would require broader model redesign

### 4. Anchor closure artifact requirements

If anchor closes, produce:

- fresh evidence file under `.sisyphus/evidence/`
- updated `.sisyphus/notepads/2026-04-02-anchor-type-system/`
- plan closure note in `docs/superpowers/plans/2026-04-02-anchor-type-system.md`
- explicit anchor verdict

---

## Part II — Tape-View Closure Re-Check

### Entry condition

Tape-view re-check executes **only if** anchor-type-system already has a closure verdict of:

- `direct closure`, or
- `minimal-tail-then-closure`

If anchor is `not a closure bucket`, tape-view does not proceed here.

Additionally, anchor closure must have already proven the anchor semantics that tape-view depends on in practice: typed anchor dispatch, anchor-aware window/tape behavior, and consumer-visible anchor handling. A generic “anchor closed” result without those dependency-relevant invariants is not sufficient to start tape-view.

### 1. Focused verification cluster

The tape-view bucket should be verified across the pipeline it claims to clean up.

At minimum, the re-check must cover:

#### Layer A — TapeView core type

Validate:

- `src/agentkit/tape/view.py`
- `tests/agentkit/tape/test_view.py`

Questions:

- Is `TapeView` implemented and exported?
- Does it correctly reflect tape windows and anchor IDs?

#### Layer B — ContextBuilder integration

Validate:

- `src/agentkit/context/builder.py`
- `tests/agentkit/context/test_builder.py`

Questions:

- Does `ContextBuilder.build()` accept `TapeView` in practice?
- Is backward compatibility with raw `Tape` preserved?

#### Layer C — Pipeline integration

Validate:

- `src/agentkit/runtime/pipeline.py`
- pipeline tests

Questions:

- Does pipeline actually construct and use `TapeView.from_tape(...)` as the plan describes?
- Is tape-view really an internal abstraction cleanup, not just a partially wired helper?

### 2. Plan / code / tests gap check

Run a line-by-line comparison against:

- `docs/superpowers/plans/2026-04-02-tape-view.md`
- current implementation files
- current tests
- matching notepad directory under `.sisyphus/notepads/2026-04-02-tape-view/`

The gap check must answer:

- whether `TapeView` is fully wired through builder and pipeline as claimed
- whether the dependency on anchor is already satisfied in the actual codebase
- whether the bucket only lacks closure proof or still hides implementation gaps

This comparison is also **evidence review**, not permission to finish historical implementation tasks unless the current bucket verdict truly depends on them.

### 3. Tape-view verdict standard

Use the same three verdicts as anchor:

- `direct closure`
- `minimal-tail-then-closure`
- `not a closure bucket`

But tape-view has an additional failure mode:

- if its current behavior still depends on unfinished anchor semantics, it cannot be closed independently even if most code is present
- if re-check reveals a second independent tape-view gap after one minimal tail fix, it must be reclassified as `not a closure bucket`

### 4. Tape-view closure artifact requirements

If tape-view closes, produce:

- fresh evidence file under `.sisyphus/evidence/`
- updated `.sisyphus/notepads/2026-04-02-tape-view/`
- plan closure note in `docs/superpowers/plans/2026-04-02-tape-view.md`
- explicit tape-view verdict

---

## Acceptance Criteria

This spec is satisfied only if it yields a clear sequential outcome.

### Successful full sequence

All of the following are true:

- anchor receives a grounded closure verdict
- if anchor closes, tape-view receives a grounded closure verdict
- neither bucket is closed by assumption alone
- each bucket produces the required closure artifacts only if it actually closes

### Successful partial sequence

This spec may still succeed partially if:

- anchor reaches `not a closure bucket`
- tape-view is explicitly stopped because its prerequisite bucket did not close

That is still a valid and useful outcome because it preserves the closure-first stop rule.

---

## Risks and Non-Goals

### Main risk

The biggest risk is treating “code exists” as proof of closure readiness.

That would create false closure on buckets whose implementation surfaces are present but whose behavioral contract, verification, or dependency wiring has not actually been proven.

### Secondary risk

The second risk is overcorrecting and turning closure re-check into reimplementation.

This spec explicitly forbids that drift by limiting each bucket to:

- re-check only
- at most one minimal blocking tail fix
- immediate verdict after re-verification

### Non-goals

- redesigning tape architecture
- merging anchor and tape-view into one combined bucket
- beginning the next closure candidate after tape-view inside this spec

---

## Recommended Next Step

Execute this spec strictly in order:

1. `anchor-type-system closure re-check`
2. decide anchor verdict
3. only if anchor closes, run `tape-view closure re-check`
4. decide tape-view verdict

The final product of this spec is:

- one anchor verdict
- one tape-view verdict (if eligible)

not a merged verdict.
