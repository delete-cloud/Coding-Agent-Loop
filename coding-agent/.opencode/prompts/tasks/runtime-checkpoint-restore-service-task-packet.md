Goal:
Move checkpoint restore runtime composition out of SessionManager so the control
plane delegates restore orchestration to a coding_agent.runs service.

Scope:
- Add a RuntimeCheckpointRestoreService under src/coding_agent/runs/.
- Make SessionManager construct the service once and delegate _restore_checkpoint().
- Preserve CheckpointRestoreService and CheckpointRuntimeBuilder behavior.
- Add focused service composition tests.
- Update ADR-0058 follow-up status.

Out of scope:
- Changing checkpoint persistence, tape truncation, or future-checkpoint pruning.
- Changing RunTarget serialization or checkpoint payload formats.
- Changing CLI, HTTP, or webui checkpoint surfaces.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/runs/checkpoint_restore.py
  - src/coding_agent/runs/checkpoint_runtime.py
  - src/coding_agent/runs/__init__.py

Target tests:
- uv run pytest tests/coding_agent/test_runtime_checkpoint_restore_service.py -v
- uv run pytest tests/coding_agent/test_checkpoint_restore_service.py tests/coding_agent/test_checkpoint_runtime_builder.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "restore_checkpoint" -v
- uv run pytest tests/ui/test_session_manager_public_api.py -k "restore_checkpoint" -v
- uv run ruff check src/coding_agent/runs/runtime_checkpoint_restore.py src/coding_agent/runs/checkpoint_restore.py src/coding_agent/runs/checkpoint_runtime.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_checkpoint_restore_service.py
- uv run ruff format --check src/coding_agent/runs/runtime_checkpoint_restore.py src/coding_agent/runs/checkpoint_restore.py src/coding_agent/runs/checkpoint_runtime.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_checkpoint_restore_service.py

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
