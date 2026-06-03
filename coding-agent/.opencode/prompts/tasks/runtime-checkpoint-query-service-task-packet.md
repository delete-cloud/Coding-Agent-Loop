Goal:
Move checkpoint metadata listing out of `SessionManager` and into the runtime query boundary.

Scope:
- Add a runtime query service for session checkpoint metadata listing.
- Preserve current behavior for sessions without a tape id and for dynamic checkpoint-service replacement in tests.
- Delegate `SessionManager.list_checkpoints()` to the new runtime query service.
- Update ADR-0058 follow-up status with the completed extraction.

Out of scope:
- Checkpoint restore behavior.
- Resume prompt construction.
- HTTP or CLI checkpoint command semantics.
- Event stream registration, cleanup, or teardown.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/query.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_query_service.py`
  - `tests/ui/test_session_manager_public_api.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_query_service.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "list_checkpoints" -v`

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
