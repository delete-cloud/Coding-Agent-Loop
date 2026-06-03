Goal:
Add a daemon-backed local non-interactive client surface so product dogfood can
target the local daemon control plane instead of the inline `run` path.

Scope:
- Preserve `coding_agent daemon` as the foreground local daemon command.
- Add `coding_agent daemon run` as an HTTP client for an already-running local
  daemon.
- Create local-path daemon sessions through `/sessions` `execution_binding`
  instead of in-process `SessionManager`.
- Stream prompts through the existing display-event HTTP stream.
- Update ADR-0058 status for the first daemon-backed local client surface.

Out of scope:
- Auto-starting or supervising a daemon process.
- Rewriting REPL/TUI to use the daemon in this slice.
- Removing the inline `coding_agent run` compatibility path.
- Adding new HTTP protocol schemas.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/cli/serve_command.py`
  - `src/coding_agent/remote/client.py`
  - `src/coding_agent/cli/main.py`
  - `tests/cli/test_remote_client.py`
  - `tests/cli/test_entrypoint_contract.py`

Target tests:
- `uv run pytest tests/cli/test_remote_client.py -k "daemon_command_starts or local_daemon or daemon_run" -v`
- `uv run pytest tests/cli/test_entrypoint_contract.py -k "daemon or subcommand_help" -v`
- `uv run ruff check src/coding_agent/cli/serve_command.py src/coding_agent/remote/client.py tests/cli/test_remote_client.py tests/cli/test_entrypoint_contract.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate if this requires daemon process supervision or a protocol migration.
- Ignore non-blocking optimization suggestions.
