Goal:
Fence `GET /sessions/{session_id}/workspace` behind the same owner and active-turn checks as other owner-sensitive HTTP session surfaces.

Scope:
- Add a `SessionManager` workspace-export guard that serializes with the session turn lock.
- Reject stale owners and active turns before exporting a cloud workspace archive.
- Revalidate ownership after archive export before returning the result.
- Cover the HTTP route with focused regression tests.

Out of scope:
- Do not add archive size or memory limits in this PR.
- Do not change workspace archive format or cloud provider behavior.
- Do not refactor unrelated HTTP/session-manager ownership logic.

Context:
- ADRs:
  - `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
  - `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
  - `docs/adr/0019-remote-client-cloud-workspace-deployment.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_http_server_workspace_transfer.py`
  - `tests/ui/test_http_server_failover.py`

Target tests:
- `uv run pytest tests/ui/test_http_server_workspace_transfer.py -v`
- `uv run pytest tests/ui/test_http_server_failover.py -k "ownership" -v`

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
