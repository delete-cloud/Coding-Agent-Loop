# G41 - CLI Entrypoint Contract

Add deterministic CLI and entrypoint smoke tests that require no provider credentials.

Scope:

- Verify `python -m coding_agent --help` exposes the expected top-level commands.
- Verify Click help for `run`, `repl`, `serve`, and `verify` works without API keys or external services.
- Verify the default no-subcommand path fails clearly in non-interactive mode and points users to batch mode.
- Do not execute real agent runs, start servers, call providers, or require production credentials.

Verification:

- `uv run pytest tests/cli/test_entrypoint_contract.py tests/cli/test_commands.py tests/cli/test_verify.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/cli/test_entrypoint_contract.py`
- `uv run ruff check tests/cli/test_entrypoint_contract.py`
- `git diff --check -- .`
