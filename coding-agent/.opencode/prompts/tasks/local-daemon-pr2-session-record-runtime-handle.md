Goal:
Split durable session metadata from process-local runtime handles as the next
ADR-0058 implementation slice, without changing runtime behavior, persisted
session payloads, or public HTTP/CLI APIs.

Scope:
- Introduce a narrow `SessionRuntimeHandle` structure for process-local fields
  such as task, runtime pipeline/context/adapter, runtime message bus, approval
  cursor, event queues, and approval coordination objects.
- Introduce or prepare a `SessionRecord` boundary for durable session metadata
  while preserving the existing stored session payload shape.
- Move access inside `SessionManager` toward explicit durable-record vs runtime
  handle usage in the smallest behavior-preserving slice.
- Add focused regression coverage proving persisted session data excludes
  process-local handles and hydrated sessions can reacquire runtime state.

Out of scope:
- Do not change HTTP request/response schemas.
- Do not change session store, runtime store, tape store, or checkpoint store
  persisted formats.
- Do not introduce `RunCoordinator` or `LocalDaemonExecutor` behavior yet.
- Do not replace `ExecutionBinding` persistence with `RunTarget` persistence in
  this slice.
- Do not change `coding_agent run` behavior.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
  - `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
  - `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
- Postmortem patterns:
  - `postmortem/patterns/PM-0015-require-store-backed-requests-across-http-approval-flow.md`
  - `postmortem/patterns/PM-0021-guard-event-stream-registration-against-disappearing-sessions.md`
  - `postmortem/patterns/PM-0022-revalidate-event-stream-ownership-after-queue-attach.md`
  - `postmortem/patterns/PM-0023-make-event-stream-cleanup-and-teardown-idempotent.md`
- Relevant files:
  - `src/coding_agent/server/session_manager.py`
  - `src/coding_agent/server/stores/session_store.py`
  - `tests/ui/test_session_persistence.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/ui/test_http_server_failover.py`

Target tests:
- `uv run pytest tests/ui/test_session_persistence.py tests/ui/test_session_manager_runtime.py -k "session or runtime or resume or checkpoint" -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py tests/ui/test_http_server_failover.py -k "event or queue or owner or close or delete" -v`
- `uv run ruff check src/coding_agent/server/session_manager.py tests/ui/test_session_persistence.py tests/ui/test_session_manager_runtime.py`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- Stop if the split requires changing persisted session payloads or public API
  schemas.
- Stop if `RunCoordinator`, executor ownership, or daemon lifecycle design is
  needed to proceed.
- Stop after one review/fix/retest cycle.
