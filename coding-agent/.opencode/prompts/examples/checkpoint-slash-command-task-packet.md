Goal:
Add a `/checkpoint` slash command flow to the REPL so the user can save, list, and restore checkpoints backed by the existing checkpoint/session infrastructure.

Scope:
- Add or update slash-command handling in `src/coding_agent/cli/commands.py` for `/checkpoint save`, `/checkpoint list`, and `/checkpoint restore <id>`.
- Wire only the minimum REPL/session context needed to invoke existing checkpoint logic in `src/coding_agent/ui/session_manager.py`.
- Add or update focused regression tests for the CLI command surface and the session-manager checkpoint path.

Out of scope:
- Do not redesign checkpoint storage, restore semantics, or plugin-state semantics.
- Do not add auto-checkpoint behavior.
- Do not refactor unrelated slash commands or REPL rendering.

Context:
- ADRs:
  - `docs/adr/0005-checkpoint-restore-uses-truncate-rollback.md`
  - `docs/adr/0006-checkpoint-plugin-state-restores-as-best-effort-hints.md`
- Relevant files:
  - `src/coding_agent/cli/commands.py`
  - `src/coding_agent/cli/repl.py`
  - `src/coding_agent/ui/session_manager.py`
  - `tests/cli/test_commands.py`
  - `tests/ui/test_session_manager_runtime.py`

Target tests:
- `uv run pytest tests/cli/test_commands.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "checkpoint_restore or plugin_states_before_mount or truncate" -v`

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
