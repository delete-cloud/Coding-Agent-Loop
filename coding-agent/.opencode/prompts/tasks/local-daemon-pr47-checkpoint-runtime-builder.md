Goal:
Move checkpoint-restored runtime construction out of SessionManager behind a narrower runtime builder service.

Scope:
- Extract direct checkpoint-restored runtime construction into a coding_agent.runs service.
- Keep local-daemon checkpoint restore preparation routed through LocalDaemonExecutor.prepare_runtime().
- Preserve provider reuse, plugin state injection before adapter initialization, and existing target validation behavior.
- Update ADR-0058 implementation status for the completed slice.

Out of scope:
- Normal non-restore runtime construction.
- Runtime close/error policy extraction.
- Live DisplayEvent SSE/UI projection.
- Sandbox wrapper defaults or daemon-backed client surfaces.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/runs/checkpoint_runtime.py
  - src/coding_agent/runs/checkpoint_restore.py
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/executors/local_daemon.py
  - tests/coding_agent/test_checkpoint_runtime_builder.py
  - tests/ui/test_session_manager_runtime.py

Target tests:
- uv run pytest tests/coding_agent/test_checkpoint_runtime_builder.py -v
- uv run pytest tests/coding_agent/test_checkpoint_restore_service.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "restore_checkpoint or restore_rejects or restore_truncates or checkpoint_restore" -v
- uv run ruff check src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_checkpoint_runtime_builder.py tests/coding_agent/test_checkpoint_restore_service.py tests/ui/test_session_manager_runtime.py

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
