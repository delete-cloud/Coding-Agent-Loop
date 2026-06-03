Goal:
Move resume-session runtime orchestration out of `SessionManager` and into the
runtime runs layer.

Scope:
- Add `RuntimeResumeOrchestrationService` alongside the existing resume context
  service.
- Delegate previous-run lookup, active-run rejection, tape id repair, resume
  context construction, boundary anchor append, local/attached dispatch, and
  resumed run lookup to the service.
- Keep `SessionManager` responsible for owner/session lookup and product
  callback wiring.
- Update ADR-0058 follow-up status.

Out of scope:
- Changing persisted run/session formats.
- Changing HTTP or CLI resume protocol behavior.
- Reworking `RuntimeResumeService` prompt/context semantics.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/runs/resume.py`
  - `tests/coding_agent/test_runtime_resume_service.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_resume_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "resume_session" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "resume" -v`
- `uv run ruff check src/coding_agent/runs/resume.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_resume_service.py`
- `uv run ruff format --check src/coding_agent/runs/resume.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_resume_service.py`

Postmortem checks:
- PM-0022 and PM-0023 were consulted because this task runs
  `tests/ui/test_session_manager_runtime.py`.
- This slice does not change `/events` queue attach, event append ownership,
  disconnect cleanup, or session teardown ordering.

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
