Goal:
Persist HTTP approval interactions into the durable runtime store.

Scope:
- Create a pending `agent_interactions` record when an approval request is
  registered during an active run.
- Resolve that interaction when the approval decision is applied or when the
  request times out.
- Map the active run plus approval `request_id` to a deterministic
  `interaction_id`.
- Preserve existing approval coordinator, runtime-message, and HTTP endpoint
  single-shot semantics.
- Keep runtime interaction persistence opt-in through the existing runtime
  store.

Out of scope:
- Do not rebuild pending approval state after process restart.
- Do not add approval replay HTTP APIs.
- Do not change approval policy behavior or session-scoped approval memory.
- Do not change AgentKit pipeline behavior.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0013-clear-answered-request-projections.md`
  - `postmortem/patterns/PM-0014-make-approval-responses-single-shot.md`
  - `postmortem/patterns/PM-0015-require-store-backed-requests-across-http-approval-flow.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`
  - `tests/approval/test_coordinator.py`
  - `tests/approval/test_store.py`

Target tests:
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "approval" -v`
- `uv run pytest tests/ui/test_http_server.py -k "ApprovalEndpoint or ApprovalStoreIntegration" -v`
- `uv run pytest tests/approval/test_coordinator.py tests/approval/test_store.py -v`
- `uv run pytest tests/coding_agent/test_pg_runtime_store.py -k "interaction" -v`
- `uv run ruff check src/coding_agent/ui/session_manager.py tests/ui/test_session_manager_runtime.py`

Stop conditions:
- Stop with a blocker if durable approval recovery requires changing runtime
  message bus persistence or HTTP approval endpoint contracts.
- Stop with a blocker if the implementation needs a second review/fix/retest
  loop.
