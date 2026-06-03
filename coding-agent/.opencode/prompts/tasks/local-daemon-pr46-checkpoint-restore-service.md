Goal:
Move checkpoint restore orchestration out of `SessionManager` into a narrower
runtime/checkpoint service boundary.

Scope:
- Add a checkpoint restore service under `coding_agent.runs`.
- Move checkpoint session config serialization/parsing, snapshot validation,
  restored tape reconstruction, session config rewind, tape truncation, runtime
  assignment, session persistence, and future-checkpoint pruning into the
  service.
- Keep public owner/turn-lock checks and restored runtime construction callbacks
  in `SessionManager` for this slice.
- Add focused service tests for invalid snapshot rejection and successful
  restore/prune/session mutation.

Out of scope:
- Sandbox policy/environment wrapper changes.
- Daemon-backed client surfaces.
- Reworking checkpoint capture storage or AgentKit checkpoint stores.
- Changing restored runtime construction behavior.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Postmortem checks:
  - PM-0020 requires focused checkpoint tests and review of checkpoint
    control-flow shape before release.
- Relevant files:
  - `src/coding_agent/runs/checkpoint_restore.py`
  - `src/coding_agent/runs/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `tests/coding_agent/test_checkpoint_restore_service.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_checkpoint_restore_service.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "restore_checkpoint or restore_rejects or restore_truncates or checkpoint_restore" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "restore_checkpoint" -v`
- `uv run pytest tests/ui/test_http_server.py -k "restore_checkpoint" -v`
- `uv run pytest tests/agentkit/checkpoint/test_service.py -v`
- `uv run ruff check src/coding_agent/runs src/coding_agent/server/session_manager.py tests/coding_agent/test_checkpoint_restore_service.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server.py`

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
