Goal:
Move workspace archive export orchestration out of `SessionManager` into a
runtime/workspace service boundary while preserving active-turn rejection,
cloud-binding validation, concurrent idle exports, export-in-progress guards,
and post-export owner revalidation.

Scope:
- Add `RuntimeWorkspaceExportService` under `coding_agent.runs`.
- Delegate `SessionManager.export_workspace_archive()` to the new service.
- Add focused service tests and update ADR-0058 follow-up status.

Out of scope:
- Do not change HTTP workspace transfer endpoints, archive generation,
  workspace cleanup, checkpoint behavior, runtime execution, daemon CLI,
  executor selection, or event streams.
- Do not change the `RuntimeTurnAdmissionService` export-in-progress guard.
- Do not touch untracked `webui/` or contract fixture workspaces.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/runs/workspace_export.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/coding_agent/test_runtime_workspace_export_service.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/ui/test_session_manager_runtime.py`
- Postmortem notes:
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_workspace_export_service.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "export_workspace_archive" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "workspace_export" -v`
- `uv run pytest tests/ui/test_http_server_failover.py -k "events or event_queue or owner" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "event_queue or close_session or clear_sessions" -v`
- `uv run ruff check src/coding_agent/runs/workspace_export.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_workspace_export_service.py`
- `uv run ruff format --check src/coding_agent/runs/workspace_export.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_workspace_export_service.py`

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
