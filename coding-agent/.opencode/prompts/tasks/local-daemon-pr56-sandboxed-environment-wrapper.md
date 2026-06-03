Goal:
Make ADR-0058 sandbox policy an explicit executor runtime environment wrapper
instead of leaving it as mixed workspace/provider config.

Scope:
- Add a `SandboxedEnvironment` wrapper that carries `RunTarget.isolation`.
- Apply the wrapper during local-daemon runtime preparation from `RunTarget`.
- Merge environment-provided tool defaults into pipeline config so shell
  sandbox defaults come from the executor/run-target boundary.
- Record ADR-0058 status for this slice.

Out of scope:
- Implement OS-native sandboxing.
- Change cloud managed execution semantics.
- Change daemon-backed client product routing.
- Change approval policy behavior.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/environment/`
  - `src/coding_agent/runs/runtime_preparation.py`
  - `src/coding_agent/app.py`
  - `tests/coding_agent/test_runtime_preparation.py`

Target tests:
- `uv run pytest tests/coding_agent/test_runtime_preparation.py -v`
- `uv run pytest tests/coding_agent/environment/test_sandboxed_environment.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "default_run_target_workspace or restore_checkpoint_uses_default_run_target_workspace or run_agent_executes_local_runtime_through_local_daemon_executor" -v`
- `uv run ruff check src/coding_agent/environment src/coding_agent/runs/runtime_preparation.py src/coding_agent/server/session_manager.py src/coding_agent/app.py tests/coding_agent/test_runtime_preparation.py tests/coding_agent/environment/test_sandboxed_environment.py tests/ui/test_session_manager_runtime.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if the slice requires changing remote protocol schemas.
- Escalate if deterministic tests require Docker, nsjail, or hosted infra.
- Ignore non-blocking optimization suggestions.
