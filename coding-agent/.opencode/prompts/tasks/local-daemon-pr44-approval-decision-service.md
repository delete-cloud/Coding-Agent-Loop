Goal:
Move approval decision publish/read/apply and session projection mutation out of
`SessionManager` into an approval service boundary.

Scope:
- Add an `ApprovalDecisionService` under `coding_agent.approval`.
- Route `SessionManager` approval runtime-message publishing, duplicate
  handling, first-write-wins reuse, cursor advancement, pending projection
  updates, approval event signaling, and durable interaction resolution through
  the service.
- Keep owner checks, session lookup, HTTP response shaping, runtime approval
  waiting, and wire message emission in `SessionManager` for this slice.
- Add focused service tests for published-decision lookup, successful
  submission, duplicate first-write behavior, stale projection rejection, and
  deferred early decisions.

Out of scope:
- Checkpoint restore preparation ownership.
- Sandbox policy/environment wrapper changes.
- Live display-event projection.
- Replacing the existing `ApprovalDecisionConsumer`.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Postmortem checks:
  - PM-0011 through PM-0015 require focused approval tests and review of the
    same approval control-flow shape before release.
- Relevant files:
  - `src/coding_agent/approval/runtime_messages.py`
  - `src/coding_agent/approval/interactions.py`
  - `src/coding_agent/approval/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `tests/coding_agent/test_approval_runtime_messages.py`
  - `tests/ui/test_session_manager_approval_runtime.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_approval_runtime_messages.py -v`
- `uv run pytest tests/ui/test_session_manager_approval_runtime.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "approval" -v`
- `uv run pytest tests/ui/test_http_server.py -k "approve_success_clears_pending_projection_for_coordinator_backed_request or approve_retry_with_changed_body_uses_first_decision or approve_rejects_stale_pending_projection_without_store_request" -v`
- `uv run ruff check src/coding_agent/approval src/coding_agent/server/session_manager.py tests/coding_agent/test_approval_runtime_messages.py tests/ui/test_session_manager_approval_runtime.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server.py`

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
