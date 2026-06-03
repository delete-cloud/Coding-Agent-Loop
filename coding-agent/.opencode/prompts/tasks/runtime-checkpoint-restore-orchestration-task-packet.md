Goal:
Move public checkpoint restore admission orchestration out of `SessionManager`.

Scope:
- Add a runtime checkpoint restore orchestration service for maintenance admission plus restore dispatch.
- Delegate `SessionManager.restore_checkpoint()` to the new orchestration service.
- Keep `SessionManager._restore_checkpoint()` as a compatibility helper for focused restore tests.
- Update ADR-0058 follow-up status with the completed extraction.

Out of scope:
- Checkpoint snapshot validation or tape truncation behavior.
- Runtime preparation behavior during restore.
- Checkpoint capture or listing behavior.
- Event stream registration, cleanup, or teardown.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/runtime_checkpoint_restore.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_checkpoint_restore_service.py`
  - `tests/ui/test_session_manager_public_api.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_checkpoint_restore_service.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "restore_checkpoint_rejects" -v`

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
