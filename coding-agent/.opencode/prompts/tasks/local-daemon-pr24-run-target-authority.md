Goal:
Make explicit `Session.default_run_target` placement authoritative over
compatibility `execution_binding` assignment.

Scope:
- Preserve legacy behavior where assigning `execution_binding` updates a
  derived default run target.
- Stop compatibility `execution_binding` assignment from overwriting an
  explicitly assigned `default_run_target`.
- Update the `Session` docstring to reflect the RunTarget-first boundary.
- Add focused persistence/runtime tests for the authority rule.

Out of scope:
- Remove `execution_binding` from persisted session payloads.
- Change HTTP session response shape.
- Add a public API for editing `default_run_target`.
- Move runtime execution into a new daemon/client path.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/server/session_manager.py
  - tests/ui/test_session_persistence.py

Target tests:
- uv run pytest tests/ui/test_session_persistence.py -k "run_target or execution_binding_assignment" -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "default_run_target_workspace or preserves_execution_binding" -v
- uv run ruff check src/coding_agent/server/session_manager.py tests/ui/test_session_persistence.py
- git diff --check

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
