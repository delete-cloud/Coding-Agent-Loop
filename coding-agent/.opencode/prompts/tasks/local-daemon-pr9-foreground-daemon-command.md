Goal:
Introduce `coding_agent daemon` as the local product entrypoint for a foreground
local control plane, without claiming background daemon lifecycle or IPC support.

Scope:
- Add a Click `daemon` command.
- Reuse the existing HTTP control-plane app and server config handling.
- Keep `serve` behavior and output stable.
- Document that `daemon` is the local product entrypoint and currently runs in
  the foreground.
- Add CLI help/startup tests for the new command.

Out of scope:
- Do not implement background process management.
- Do not implement local socket/IPC transport.
- Do not rewrite REPL into a daemon client in this slice.
- Do not change HTTP API behavior.
- Do not change session/runtime persistence schemas.

Context:
- ADR: `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/cli/main.py`
  - `src/coding_agent/cli/serve_command.py`
  - `tests/cli/test_entrypoint_contract.py`
  - `tests/cli/test_remote_client.py`
  - `README.md`
  - `docs/dogfood/CURRENT_STATE.md`

Target tests:
- `uv run pytest tests/cli/test_entrypoint_contract.py -v`
- `uv run pytest tests/cli/test_remote_client.py -k "serve_config or daemon_command" -v`
- `uv run ruff check src/coding_agent/cli/main.py src/coding_agent/cli/serve_command.py tests/cli/test_entrypoint_contract.py tests/cli/test_remote_client.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited, run a local subagent P1/P2 review before merge.
