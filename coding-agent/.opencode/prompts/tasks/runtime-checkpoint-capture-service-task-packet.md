Goal:
Move checkpoint capture orchestration out of `SessionManager` into a runtime
service boundary while preserving the existing maintenance admission, checkpoint
metadata schema, and runtime context behavior.

Scope:
- Add `RuntimeCheckpointCaptureService` under `coding_agent.runs`.
- Delegate `SessionManager.capture_checkpoint()` operation body to the new
  service after `RuntimeMaintenanceAdmissionService` admits the session.
- Add focused service tests and update ADR-0058 follow-up status.

Out of scope:
- Do not change checkpoint restore, runtime config replacement, checkpoint
  payload schema, HTTP endpoints, daemon CLI behavior, executor selection, or
  event stream behavior.
- Do not change maintenance admission ordering or lock behavior.
- Do not touch untracked `webui/` or contract fixture workspaces.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/checkpoint_capture.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_checkpoint_capture_service.py`
  - `tests/ui/test_session_manager_public_api.py`
- Postmortem notes:
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_checkpoint_capture_service.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "capture_checkpoint" -v`
- `uv run pytest tests/ui/test_http_server_failover.py -k "events or event_queue or owner" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "event_queue or close_session or clear_sessions" -v`
- `uv run ruff check src/coding_agent/runs/checkpoint_capture.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_checkpoint_capture_service.py`
- `uv run ruff format --check src/coding_agent/runs/checkpoint_capture.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_checkpoint_capture_service.py`

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
