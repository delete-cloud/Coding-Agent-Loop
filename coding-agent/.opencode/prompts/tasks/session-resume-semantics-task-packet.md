Goal:
Implement Codex/Claude Code-style session resume semantics: do not restore old
processes; create a new run linked to the previous run and inject resume context.

Scope:
- Add ADR-0055 for session resume semantics.
- Add SessionManager resume preparation and run metadata linkage.
- Add `POST /sessions/{session_id}/resume` for server-managed and local-attached
  executor sessions.
- Preserve previous run state and reject resume while a latest durable run is
  still active.
- Add focused tests for runtime metadata linkage and HTTP resume behavior.

Out of scope:
- Local `coding_agent resume` CLI.
- `remote resume` CLI.
- Process-level reconnect, local daemon, lease/fencing, event spool, registry,
  cross-machine workspace sync, and pool UI.
- Automatic checkpoint rollback.

Context:
- ADRs:
  - `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
  - `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
  - `docs/adr/0032-durable-runtime-lifecycle-statuses.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/server/http_server.py`
  - `src/coding_agent/server/schemas.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -k "resume_session" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -k "resume_session or external_worker" -v`

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
