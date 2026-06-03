Goal:
Add a daemon-backed local REPL client surface so interactive local dogfood can
connect to the local daemon control plane instead of owning an in-process
runtime manager.

Scope:
- Add `coding_agent daemon repl` as an HTTP client for an already-running local
  daemon.
- Create one local-path session through `/sessions` `execution_binding`.
- Send each entered prompt through the existing display-event HTTP stream.
- Preserve `coding_agent daemon` foreground server and `coding_agent daemon run`
  behavior.
- Update ADR-0058 daemon-client status.

Out of scope:
- Rewriting the existing rich in-process `coding_agent repl`.
- Adding TUI daemon pairing.
- Auto-starting or supervising a daemon process.
- Removing the inline testkit/devkit `run` path.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/cli/serve_command.py`
  - `tests/cli/test_remote_client.py`
  - `tests/cli/test_entrypoint_contract.py`

Target tests:
- `uv run pytest tests/cli/test_remote_client.py -k "daemon_command_starts or local_daemon or daemon_run or daemon_repl" -v`
- `uv run pytest tests/cli/test_entrypoint_contract.py -k "daemon or subcommand_help" -v`
- `uv run ruff check src/coding_agent/cli/serve_command.py tests/cli/test_remote_client.py tests/cli/test_entrypoint_contract.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if this requires process supervision, TUI redesign, or a protocol
  migration.
- Ignore non-blocking optimization suggestions.
