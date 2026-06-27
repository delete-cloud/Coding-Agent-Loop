Goal:
Add a minimal remote CLI wrapper for the semantic memory maintenance HTTP API
landed in PR #638 so operators can inspect semantic memory status and trigger an
explicit rebuild from a named remote.

Scope:
- Add thin HTTP client helpers in `src/coding_agent/remote/client.py`:
  - `get_remote_semantic_memory_status(endpoint, session_id)`
  - `rebuild_remote_semantic_memory(endpoint, session_id, batch_size, allow_rebuild)`
- Add CLI commands in `src/coding_agent/cli/remote_commands.py`:
  - `remote memory status NAME --session SESSION_ID`
  - `remote memory rebuild NAME --session SESSION_ID --confirm [--batch-size N] [--allow-rebuild]`
- Add focused tests in `tests/cli/test_remote_client.py` proving the commands
  call the exact HTTP endpoints, preserve bearer auth, pass the expected JSON
  body, format useful output, and reject rebuild without `--confirm`.

Out of scope:
- Do not change server HTTP endpoints, schemas, auth, or SessionManager rebuild
  behavior.
- Do not add or change semantic vector backends.
- Do not enable `[memory.semantic]` by default.
- Do not add REPL slash commands, Web UI, Helm/o6n values, or new ADRs.
- Do not reinterpret `allow_rebuild` as user confirmation.

Context:
- ADR:
  - `docs/adr/0072-tape-memory-semantic-index-and-enable-switch.md`
- Prior task packet:
  - `.opencode/prompts/tasks/semantic-memory-maintenance-surface-task-packet.md`
- Relevant files:
  - `src/coding_agent/remote/client.py`
  - `src/coding_agent/cli/remote_commands.py`
  - `tests/cli/test_remote_client.py`
  - `src/coding_agent/server/schemas.py`
  - `src/coding_agent/server/http_server.py`

Design constraints:
- This is a product-layer CLI thin client over the already accepted HTTP API.
  It should live in `coding_agent`, not `agentkit`.
- Keep `allow_rebuild` semantics precise: it controls backend schema rebuild
  permission. It is not a confirmation flag for ordinary destructive document
  clearing.
- The CLI rebuild command must require a separate explicit `--confirm` flag
  before making the POST request, because rebuild clears/replaces derived
  semantic documents.
- The default `--batch-size` should match the existing server examples/tests
  and remain within the server schema boundary: use `10`.
- `--allow-rebuild` defaults false and only sets request body
  `allow_rebuild=true` when explicitly passed.
- Use existing remote client helpers (`_get_remote_json`, `_post_remote_json`)
  and existing error propagation; do not add parallel error handling.
- Use `_print_mapping` for stable, inspectable output. Avoid printing raw memory
  body text; the server status/rebuild responses are counts and document ids.
- Preserve existing command behavior and command names.

Expected HTTP contract:
- Status:
  - Method: `GET`
  - Path: `/sessions/{session_id}/memory/semantic/status`
  - Body: none
- Rebuild:
  - Method: `POST`
  - Path: `/sessions/{session_id}/memory/semantic/rebuild`
  - Body: `{"batch_size": <int>, "allow_rebuild": <bool>}`

Target tests:
- `uv run pytest tests/cli/test_remote_client.py -k "semantic_memory or remote_memory" -v`
- `uv run ruff check src/coding_agent/remote/client.py src/coding_agent/cli/remote_commands.py tests/cli/test_remote_client.py`

Review gate:
- Reviewer reports only P1/P2 issues.
- P1 examples: wrong endpoint/path, missing auth, rebuild can run without
  explicit confirmation, `allow_rebuild` used as confirmation, server behavior
  changed, or commands placed in the wrong layer.
- P2 examples: weak output assertions, missing JSON body assertion, batch-size
  validation mismatch, or brittle command names inconsistent with local CLI
  conventions.

Stop conditions:
- Stop when target tests and ruff pass and read-only review finds no P1/P2.
- Escalate if reviewer argues this needs a new ADR or a different CLI shape.
