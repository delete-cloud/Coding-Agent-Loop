Goal:
Add the missing ADR-0019 remote approval prompt choices so remote attach/repl users can approve once, approve for the session, or reject with feedback from the CLI.

Scope:
- Extend `src/coding_agent/remote/client.py` remote approval prompting to capture session-scope approval and rejection feedback.
- Add focused CLI regression tests for the remote approval request flow.
- Keep the change local to the remote client approval UX without changing server approval semantics.

Out of scope:
- Do not change HTTP approval schema or session-manager approval coordination.
- Do not add retries, validation loops, or broader remote attach UX refactors.

Context:
- ADRs:
  - `docs/adr/0019-remote-client-cloud-workspace-deployment.md`
- Relevant files:
  - `src/coding_agent/remote/client.py`
  - `src/coding_agent/ui/approval_prompt.py`
  - `tests/cli/test_remote_client.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/cli/test_remote_client.py -k "approval_request or formats_approval" -v`
- `uv run pytest tests/ui/test_http_server.py -k "approval and session" -v`
- `uv run basedpyright --level error src/coding_agent/remote/client.py tests/cli/test_remote_client.py`

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
