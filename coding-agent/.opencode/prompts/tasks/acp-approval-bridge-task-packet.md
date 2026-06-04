Goal:
Add ACP `session/request_permission` bridging for Coding Agent approval requests
and ship it through the GitHub PR lifecycle.

Scope:
- Add agent-originated JSON-RPC request support to the ACP stdio transport.
- Map internal `ApprovalRequest` wire messages to ACP `session/request_permission`.
- Translate selected/cancelled ACP permission outcomes back into
  `SessionManager.submit_approval_response`.
- Cover allow once, allow for session, reject/cancel, and stdio response routing.

Out of scope:
- `session/load` / `session/resume`.
- Terminal, filesystem, MCP server bridging.
- Cross-session remembered permissions.
- External editor compatibility harness.

Context:
- ADRs:
  - `docs/adr/0061-acp-stdio-adapter.md`
  - `docs/adr/0062-acp-approval-permission-bridge.md`
- Relevant files:
  - `src/coding_agent/acp/server.py`
  - `src/coding_agent/acp/mapper.py`
  - `src/coding_agent/cli/local_runtime.py`
  - `tests/acp/test_server.py`

Target tests:
- `uv run pytest tests/acp -k "permission or stdio" -v`
- `uv run pytest tests/cli/test_entrypoint_contract.py -k acp -v`
- `uv run ruff check src/coding_agent/acp src/coding_agent/cli/local_runtime.py tests/acp tests/cli/test_entrypoint_contract.py`

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
