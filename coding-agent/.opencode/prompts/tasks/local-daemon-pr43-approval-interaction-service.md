Goal:
Move durable approval interaction create/resolve persistence out of
`SessionManager` into an approval service boundary.

Scope:
- Add an `ApprovalInteractionService` under `coding_agent.approval`.
- Route `SessionManager` approval interaction creation/resolution through the
  service.
- Keep approval waiting, runtime-message consumption, and session projection
  mutation in `SessionManager` for this slice.
- Add focused service tests for interaction creation, resolution, and no-op
  behavior.

Out of scope:
- Approval request waiting/session state extraction.
- Approval runtime-message consumption extraction.
- Checkpoint restore preparation ownership.
- Runtime close/error policy extraction.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/approval/interactions.py`
  - `src/coding_agent/approval/__init__.py`
  - `src/coding_agent/server/session_manager.py`
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `tests/coding_agent/test_approval_interactions.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/coding_agent/test_approval_runtime_messages.py`

Target tests:
- `uv run pytest tests/coding_agent/test_approval_interactions.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "approval_interaction" -v`
- `uv run pytest tests/coding_agent/test_approval_runtime_messages.py -k "submit_approval" -v`
- `uv run ruff check src/coding_agent/approval src/coding_agent/server/session_manager.py tests/coding_agent/test_approval_interactions.py tests/ui/test_session_manager_runtime.py tests/coding_agent/test_approval_runtime_messages.py`

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
