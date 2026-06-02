Goal:
Demote `coding_agent run --patch` from a visible product-style capability to a deprecated dev/testkit compatibility flag.

Scope:
- Hide `run --patch` and `run --verify-cmd` from normal CLI help.
- Emit a deprecation warning when either hidden compatibility flag is used.
- Keep existing compatibility behavior for scripts and dogfood task packets.
- Update README wording to keep `run` as dev/testkit one-shot only.

Out of scope:
- Remove `coding_agent run`.
- Remove `--patch` or `--verify-cmd` behavior.
- Build the daemon-backed non-interactive client path.
- Change REPL, daemon, serve, or remote client behavior.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - src/coding_agent/cli/main.py
  - tests/cli/test_entrypoint_contract.py
  - README.md

Target tests:
- uv run pytest tests/cli/test_entrypoint_contract.py -k "run_help or run_patch_mode" -v
- uv run pytest tests/cli/test_entrypoint_contract.py -v
- uv run ruff check src/coding_agent/cli/main.py tests/cli/test_entrypoint_contract.py
- git diff --check

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
