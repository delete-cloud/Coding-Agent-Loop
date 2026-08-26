Goal:
Address accepted P1 review findings in `ForkTapeStore` without changing the broader checkpoint design.

Scope:
- Fix only the accepted P1 issues in `src/agentkit/tape/store.py`.
- Add or update only the regression tests needed in `tests/agentkit/tape/test_store.py`.
- Keep the stable-base-id semantics from ADR-0002 unchanged.

Out of scope:
- Do not redesign `ForkTapeStore.commit()` semantics.
- Do not modify checkpoint restore, session continuity, or slash-command behavior.
- Do not do P3 cleanup, style-only refactors, or unrelated test rewrites.

Accepted findings:
- P1: `rollback()` must clear `_base_lengths` and `_base_tape_ids` so abandoned forks do not leave stale internal tracking state behind.
- P1: if `commit()` fails while saving to the backing store, the fork must not end up in a broken intermediate state that causes duplicate delta persistence or a false finalized state on retry.

Context:
- ADRs:
  - `docs/adr/0002-fork-tape-store-commits-to-stable-base-id.md`
- Relevant files:
  - `src/agentkit/tape/store.py`
  - `tests/agentkit/tape/test_store.py`

Target tests:
- `uv run pytest tests/agentkit/tape/test_store.py -k "rollback_cleans_internal_tracking_state or commit_save_failure_does_not_duplicate_delta_on_retry or second_commit_appends_to_same_base_tape_id" -v`
- `uv run pytest tests/agentkit/tape/test_store.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
