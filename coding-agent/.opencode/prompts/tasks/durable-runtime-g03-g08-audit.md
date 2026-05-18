Goal:
Audit and close active durable runtime G03 and G08 progress gaps.

Scope:
- Confirm G03 storage/plugin/composition wiring preserves JSONL defaults and
  only enables PostgreSQL runtime storage when `storage.runtime_backend = "pg"`.
- Confirm G08 observability correlation propagates allowed runtime identity
  keys and rejects unsafe raw prompt/content/message/result/secret/text-style
  attributes.
- Add focused test coverage only where the audit evidence is too implicit.
- Update `docs/durable_runtime/GOAL_PROGRESS.md`.

Out of scope:
- Do not change production storage behavior.
- Do not change OTLP/Langfuse exporter behavior.
- Do not introduce new runtime stores, endpoints, or migrations.
- Do not work on active G09 tape debug APIs or active G11 smoke docs/tests in
  this slice.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0030-postgresql-durable-runtime-store.md`
  - `docs/adr/0032-durable-runtime-lifecycle-statuses.md`
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0006-add-usage-event-fields-and-fix-tool-name-kwarg-in-pipeline.md`
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
  - `postmortem/patterns/PM-0010-route-incremental-context-append-through-tapeview.md`
- Relevant files:
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/observability.py`
  - `src/agentkit/runtime/pipeline.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/agentkit/runtime/test_pipeline.py`
  - `tests/coding_agent/test_observability.py`
  - `docs/durable_runtime/GOAL_PROGRESS.md`

Target tests:
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "storage_config or runtime_store or pg_backends or pg_pool or runtime_backend" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "span" -v`
- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run ruff check tests/agentkit/runtime/test_pipeline.py`
- `uv run ruff format --check tests/agentkit/runtime/test_pipeline.py`

Loop policy:
- Engineer implements the smallest evidence/coverage change and runs target
  tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- Stop with a blocker if G03 requires production storage changes.
- Stop with a blocker if G08 requires weakening the observability denylist.
- Stop with a blocker if this audit expands into G09 tape debug work.
