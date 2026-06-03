Goal:
Move local runtime maintenance turn admission out of `SessionManager` so
checkpoint capture, checkpoint restore, and runtime config replacement share a
small service boundary for turn-lock, owner, session-load, and active-turn
checks.

Scope:
- Add a `RuntimeMaintenanceAdmissionService` under `coding_agent.runs` with an
  exclusive admission helper for maintenance operations.
- Delegate `SessionManager.replace_session_runtime_config()`,
  `SessionManager.capture_checkpoint()`, and
  `SessionManager.restore_checkpoint()` admission checks to the new service.
- Add focused service tests and update ADR-0058 follow-up status.

Out of scope:
- Do not change checkpoint payload shape, tape truncation, runtime replacement,
  HTTP event stream behavior, daemon CLI behavior, or executor selection.
- Do not change `run_agent()` or `prepare_session_turn()` behavior already owned
  by `RuntimeTurnAdmissionService`.
- Do not touch untracked `webui/` or contract fixture workspaces.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/lifecycle.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_maintenance_admission_service.py`
  - `tests/ui/test_session_manager_runtime.py`
- Postmortem notes:
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_maintenance_admission_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "checkpoint or runtime_config or turn_in_progress" -v`
- `uv run ruff check src/coding_agent/runs/lifecycle.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_maintenance_admission_service.py`
- `uv run ruff format --check src/coding_agent/runs/lifecycle.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_maintenance_admission_service.py`

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
