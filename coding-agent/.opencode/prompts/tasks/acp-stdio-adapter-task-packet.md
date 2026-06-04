Goal:
Implement an ACP stdio adapter MVP for Coding Agent and ship it through the
GitHub PR lifecycle.

Scope:
- Add `src/coding_agent/acp/` as a product-layer ACP JSON-RPC adapter.
- Add a CLI entry point that runs the ACP server over stdin/stdout.
- Support `initialize`, `session/new`, `session/prompt`, and `session/cancel`.
- Convert Coding Agent `WireMessage` values into ACP `session/update`
  notifications.
- Keep stdout reserved for JSON-RPC messages.

Out of scope:
- ACP `session/load`, `session/resume`, session list, and session close.
- Client-hosted filesystem or terminal methods.
- Full interactive approval bridging.
- Any rewrite of the existing agent loop or HTTP daemon.

Context:
- ADRs:
  - `docs/adr/0061-acp-stdio-adapter.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/wire/protocol.py`
  - `src/coding_agent/cli/main.py`
  - `src/coding_agent/plugins/mcp.py`

Target tests:
- `uv run pytest tests/acp -v`
- `uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v`
- `uv run ruff check src/coding_agent/acp src/coding_agent/cli/main.py tests/acp tests/cli/test_entrypoint_contract.py`

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
