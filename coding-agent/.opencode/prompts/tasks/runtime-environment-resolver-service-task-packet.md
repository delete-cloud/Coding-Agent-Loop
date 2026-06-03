Goal:
Move RunTarget environment resolution out of SessionManager and make checkpoint
restore plus local daemon runtime preparation share one coding_agent.runs service.

Scope:
- Add a RuntimeEnvironmentResolverService under src/coding_agent/runs/.
- Delegate SessionManager checkpoint restore environment callbacks to that service.
- Delegate LocalDaemonRuntimePreparationService environment resolution to that service.
- Add focused tests for local, cloud, unsupported workspace, and workspace-root behavior.

Out of scope:
- Changing RunTarget serialization or persisted session records.
- Changing executor selection, checkpoint restore semantics, or webui behavior.
- Removing compatibility ExecutionBinding fields.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/runs/runtime_preparation.py
  - src/coding_agent/runs/checkpoint_runtime.py
  - src/coding_agent/runs/__init__.py

Target tests:
- uv run pytest tests/coding_agent/test_runtime_environment.py -v
- uv run pytest tests/coding_agent/test_runtime_preparation.py tests/coding_agent/test_checkpoint_runtime_builder.py -v
- uv run pytest tests/ui/test_session_manager_runtime.py -k "ensure_session_runtime or replace_session_runtime_config or checkpoint_restore or runtime_metadata" -v
- uv run ruff check src/coding_agent/runs/environment.py src/coding_agent/runs/runtime_preparation.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_environment.py
- uv run ruff format --check src/coding_agent/runs/environment.py src/coding_agent/runs/runtime_preparation.py src/coding_agent/runs/__init__.py src/coding_agent/server/session_manager.py tests/coding_agent/test_runtime_environment.py

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
