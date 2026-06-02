Goal:
Wire the ADR-0058 `RunCoordinator` boundary into the real
`SessionManager.run_agent()` path without moving runtime ownership yet.

Scope:
- Allow `SessionManager` to accept an injectable `RunCoordinator`, defaulting to
  `DefaultRunCoordinator`.
- Convert the session's compatibility `ExecutionBinding` into a canonical
  `RunTarget` for every local `run_agent()` turn.
- Submit a `RunRequest` through the coordinator after the durable run record is
  created and before the runtime is marked running.
- Preserve current runtime execution behavior: `SessionManager` still owns the
  runtime loop in this slice.
- Add focused regression coverage proving the real run path submits the
  expected `RunRequest` and preserves the existing run lifecycle.

Out of scope:
- Do not introduce `LocalDaemonExecutor`.
- Do not move pipeline/context/adapter ownership out of `SessionManager`.
- Do not change HTTP or CLI APIs.
- Do not change persisted session payloads or runtime store schemas.
- Do not replace persisted `ExecutionBinding` with persisted `RunTarget`.
- Do not demote `coding_agent run` in this slice.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/runs/coordinator.py`
  - `src/coding_agent/runs/target.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/coding_agent/test_run_coordinator.py`
  - `tests/coding_agent/test_run_target.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -v`
- `uv run pytest tests/coding_agent/test_run_target.py tests/coding_agent/test_run_coordinator.py -v`
- `uv run ruff check src/coding_agent/server/session_manager.py src/coding_agent/runs tests/ui/test_session_manager_runtime.py tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py`
- `uv run basedpyright src/coding_agent/runs tests/coding_agent/test_run_coordinator.py tests/coding_agent/test_run_target.py`
- `git diff --check`

Known type-check caveat:
- `uv run basedpyright src/coding_agent/server/session_manager.py tests/ui/test_session_manager_runtime.py`
  currently reports pre-existing strict typing errors in the large session
  manager test surface. This PR must not add new error lines there.

Loop policy:
- Engineer implements the smallest correct boundary wiring and runs the target
  tests.
- Reviewer reports only P1/P2 correctness, safety, or scope issues.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Stop after one review/fix/retest cycle unless a human expands the scope.

Stop conditions:
- Stop if coordinator integration requires changing persisted schemas.
- Stop if runtime ownership must move to proceed.
- Stop if `LocalDaemonExecutor` design becomes necessary for this slice.
