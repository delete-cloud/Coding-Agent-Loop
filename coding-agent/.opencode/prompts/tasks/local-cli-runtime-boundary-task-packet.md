Goal:
Separate the local interactive CLI runtime from the HTTP/control-plane session
manager while preserving current REPL behavior.

Scope:
- Identify the smallest behavior-preserving boundary between
  `coding_agent.cli.repl.InteractiveSession` and server session management.
- Introduce or prepare a local CLI runtime/session abstraction for pipeline,
  adapter, tape id, approval memory, model switching, and checkpoint restore.
- Remove direct local REPL dependence on remote/control-plane-only concepts from
  `server.SessionManager` where practical in the first slice.

Out of scope:
- Renaming HTTP endpoints or remote CLI commands.
- Moving executor, Bee, Docker, or workspace provider modules.
- Changing checkpoint restore semantics or tape storage formats.

Context:
- ADRs:
  - `docs/adr/0048-application-structure-refactor-boundaries.md`
  - `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
  - `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
  - `docs/adr/0056-local-cli-control-plane-and-workspace-product-boundaries.md`
- Relevant files:
  - `src/coding_agent/cli/repl.py`
  - `src/coding_agent/cli/commands.py`
  - `src/coding_agent/app.py`
  - `src/coding_agent/adapter.py`
  - `src/coding_agent/server/session_manager.py`
  - `tests/cli/test_repl.py`
  - `tests/cli/test_commands.py`

Target tests:
- `uv run pytest tests/cli/test_repl.py tests/cli/test_commands.py -k "managed_session or model or checkpoint or process_message" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_runtime.py -k "file_session_store or checkpoint_restore or runtime" -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if local CLI behavior requires changing HTTP server semantics.
- Escalate if the slice would require migrating persisted session data.
- Ignore non-blocking optimization suggestions.
