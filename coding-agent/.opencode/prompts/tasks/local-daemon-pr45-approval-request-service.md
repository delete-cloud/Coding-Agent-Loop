Goal:
Move approval request waiting/session setup mutation out of `SessionManager`
into an approval service boundary.

Scope:
- Add an `ApprovalRequestService` under `coding_agent.approval`.
- Route session-scope auto-approval, pending request registration, pre-published
  decision handoff, wait-response projection, timeout interaction resolution,
  and wait cleanup through the service.
- Keep owner checks, session lookup, turn-in-progress checks, wire emission, and
  the actual `ApprovalCoordinator.wait_for_response()` await in `SessionManager`.
- Add focused service tests for pending setup, pre-published decision handoff,
  and wait response cleanup behavior.

Out of scope:
- Checkpoint restore preparation ownership.
- Sandbox policy/environment wrapper changes.
- Live display-event projection.
- Replacing `ApprovalDecisionService` or `ApprovalInteractionService`.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Postmortem checks:
  - PM-0011 through PM-0015 require focused approval tests and review of the
    same approval control-flow shape before release.
- Relevant files:
  - `src/coding_agent/approval/requests.py`
  - `src/coding_agent/approval/runtime_messages.py`
  - `src/coding_agent/approval/interactions.py`
  - `src/coding_agent/approval/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `tests/coding_agent/test_approval_requests.py`
  - `tests/coding_agent/test_approval_runtime_messages.py`
  - `tests/ui/test_session_manager_approval_runtime.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_approval_requests.py -v`
- `uv run pytest tests/coding_agent/test_approval_runtime_messages.py -v`
- `uv run pytest tests/ui/test_session_manager_approval_runtime.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "approval" -v`
- `uv run pytest tests/ui/test_http_server.py -k "approve_success_clears_pending_projection_for_coordinator_backed_request or approve_retry_with_changed_body_uses_first_decision or approve_rejects_stale_pending_projection_without_store_request" -v`
- `uv run ruff check src/coding_agent/approval src/coding_agent/server/session_manager.py tests/coding_agent/test_approval_requests.py tests/coding_agent/test_approval_runtime_messages.py tests/ui/test_session_manager_approval_runtime.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server.py`

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
