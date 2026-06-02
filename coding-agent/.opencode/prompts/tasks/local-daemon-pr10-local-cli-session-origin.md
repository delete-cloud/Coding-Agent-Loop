Goal:
Record explicit durable origin metadata for local CLI-created sessions so
dev/testkit one-shot runs and REPL sessions do not look like unclassified
control-plane sessions.

Scope:
- Add a small local CLI origin helper.
- Pass origin metadata when `coding_agent run` creates its compatibility
  one-shot session.
- Pass origin metadata when REPL creates managed local CLI sessions.
- Add focused CLI tests that assert the origin payload.

Out of scope:
- Do not change runtime execution behavior.
- Do not make REPL a daemon client yet.
- Do not change HTTP/server session creation behavior.
- Do not change persisted session schema; `origin` already exists.

ADR:
- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Relevant files:
- `src/coding_agent/cli/local_runtime.py`
- `src/coding_agent/cli/main.py`
- `src/coding_agent/cli/repl.py`
- `tests/cli/test_entrypoint_contract.py`
- `tests/cli/test_repl.py`

Target tests:
- `uv run pytest tests/cli/test_entrypoint_contract.py -k "run_command_uses_managed_session" -v`
- `uv run pytest tests/cli/test_repl.py -k "initialize_creates_managed_session_without_asyncio_run" -v`
- `uv run ruff check src/coding_agent/cli/local_runtime.py src/coding_agent/cli/main.py src/coding_agent/cli/repl.py tests/cli/test_entrypoint_contract.py tests/cli/test_repl.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited or skipped, run a local subagent P1/P2 review
  before merge.
