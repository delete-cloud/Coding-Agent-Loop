# Task Packet

packet_id: tp-2026-08-30-adr-0083-phase-c-hotfix
packet_revision: 1
role: implementer
baseline_ref: origin/main
baseline_sha: bb8b5d03a3d9140d7b695e240204963b73ce502d
branch: fix/adr-0083-phase-c-review

## Goal

Correct the ten Phase C runtime/API defects found by the post-merge runtime and boundary reviews before starting Phase D.

## Scope

- Ensure internally produced effect settlements commit before any safe yield.
- Convert post-dispatch executor exceptions into indeterminate settlements.
- Persist committed assistant/tool conversation into the next `ModelRequest` context.
- Make transition identity stable for one consume-once input across state revisions.
- Persist and validate the active model request identity on resumed completions.
- Accept and recursively freeze any host-neutral JSON effect result.
- Recursively thaw committed tool payloads before legacy wire projection.
- Put failed effect messages in the tool-result payload.
- Separate interrupt stop reporting from non-settling `SafeYield` behavior.
- Preserve durable `cancelled` status while retaining legacy `INTERRUPTED` stop reason.

Allowed production files:

- `src/agentkit/runtime/contracts.py`
- `src/agentkit/runtime/engine.py`
- `src/agentkit/runtime/coordinator.py`
- `src/agentkit/runtime/__init__.py`
- `src/agentkit/__init__.py`
- `src/coding_agent/adapter/pipeline.py`
- `src/coding_agent/adapter/types.py`
- `src/coding_agent/runs/lifecycle.py`

Allowed tests are the matching Phase C runtime, adapter, and lifecycle test files.

## Authority

- `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`
- `.opencode/prompts/packets/tp-2026-08-30-adr-0083-phase-c.md`
- `postmortem/patterns/PM-0006-add-usage-event-fields-and-fix-tool-name-kwarg-in-pipeline.md`
- `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
- `postmortem/patterns/PM-0010-route-incremental-context-append-through-tapeview.md`
- `postmortem/patterns/PM-0026-never-persist-blank-exception-messages-as-run-errors.md`

## Non-goals

- No Phase D WAL, live coordinator cutover, mailbox migration, or durable host ports.
- No new persistence schema or wire message type.
- No legacy pipeline algorithm change.
- No unrelated refactor.

## Acceptance criteria

- Each reported defect has a regression test observed failing before its fix.
- Scalar and nested JSON effect results survive contracts, committed notices, redaction, and wire serialization.
- Effect execution never returns after dispatch without a completed, failed, or indeterminate settlement commit attempt.
- Replayed consume-once inputs keep one transition identity independent of revision.
- Resumed model completions must match the active request identity stored in committed state.
- Safe yield reports the compatibility interrupt without producing a durable root outcome.
- Cancelled reports legacy `INTERRUPTED` and persists durable root status `cancelled`.
- Original Phase C target suites, affected lifecycle tests, Ruff checks, and the postmortem release checks pass.
- One bounded P1/P2 review, one accepted-fix pass, and one verifier retest complete.

## Stop conditions

- Stop if a fix requires a Phase D port signature change, live-path cutover, or new persisted/wire schema.
- Stop after one `review -> accepted fixes -> retest` cycle.
