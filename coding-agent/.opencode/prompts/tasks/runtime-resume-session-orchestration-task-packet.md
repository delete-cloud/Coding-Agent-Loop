Goal:
Move public resume-session orchestration out of `SessionManager`.

Scope:
- Add a runtime resume session orchestration service for runtime-store requirement, owner assertion, session loading, and resume dispatch.
- Construct the resume orchestration service once during `SessionManager` initialization with dynamic providers for runtime store and checkpoint lookup.
- Delegate `SessionManager.resume_session()` to the new service.
- Update ADR-0058 follow-up status with the completed extraction.

Out of scope:
- Resume prompt content or metadata semantics.
- Local or attached executor resume dispatch behavior.
- Runtime event replay or display-event behavior.
- Event stream registration, cleanup, or teardown.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/resume.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_resume_service.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_resume_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "resume_session" -v`

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
