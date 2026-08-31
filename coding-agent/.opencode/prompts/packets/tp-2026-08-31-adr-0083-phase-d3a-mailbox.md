# Task Packet

packet_id: tp-2026-08-31-adr-0083-phase-d3a-mailbox
packet_revision: 1
role: implementer
baseline_ref: origin/main
baseline_sha: e7593298a40efeeae37464b507cc2a482f74fe96
branch: feat/adr-0084-phase-d3a-mailbox

## Goal

Implement ADR-0084 Phase D3a only: additive durable runtime-command admission and a monotonic dispatch generation/cut on SQLite and PostgreSQL, without activating the new runtime path.

## Scope

- Extend `session_fact_source` with non-negative `dispatch_generation`, migrated with default `0`.
- Extend `session_mailbox_slots` with nullable runtime-command admission sequence/generation metadata while preserving legacy turn/mailbox rows.
- Add immutable host-side command-mailbox admission/snapshot values and a checked conflict error.
- Add fenced `admit_runtime_command` and consistent pending-command snapshot reads to both durable backends.
- A new admission allocates one global `session_seq`; only `cancel`, `interrupt`, or an `approval_decision` with `approved=false` advances dispatch generation.
- Exact replay of the same command ID and content is idempotent and advances neither counter. Reusing an ID for different content fails before mutation.
- Preserve pending command payload when a later typed transition writes its disposition.

## Non-goals

- No approval or subagent publisher/consumer cutover.
- No coordinator activation or durable `CommitPort` implementation.
- No dispatch authorization transaction.
- No effect writer migration.
- Do not remove `settled`, effect ranks, or legacy runtime messages.
- No Phase D4 reconciliation, Phase E ports, or Phase F `EventRecord` cutover.
- No Phase C public API changes.

## Allowed production files

- `src/coding_agent/stores/rtstore/harness.py`
- `src/coding_agent/stores/runtime_store.py`
- `src/coding_agent/stores/runtime.py`
- `src/coding_agent/stores/local_durable/core.py`
- `src/coding_agent/stores/local_durable/fact_source.py`
- `src/coding_agent/stores/local_durable/fact_source_rows.py`
- `src/coding_agent/stores/local_durable/uow.py`
- `src/coding_agent/stores/pg_durable/core.py`
- `src/coding_agent/stores/pg_durable/fact_source.py`
- `src/coding_agent/stores/pg_durable/fact_source_rows.py`
- `src/coding_agent/stores/pg_durable/sql_harness.py`
- `src/coding_agent/stores/pg_durable/uow.py`
- matching package exports only if required

Allowed tests are the matching SQLite/PostgreSQL fencing, fact-source, Phase B UoW, and schema-upgrade tests.

## Acceptance criteria

- `test_mailbox_admission_advances_dispatch_generation_sqlite`
- `test_mailbox_admission_advances_dispatch_generation_postgresql`
- Approval allow and non-control commands allocate `session_seq` without advancing generation.
- Cancel, interrupt, and approval denial advance generation exactly once.
- Exact replay is idempotent; conflicting replay writes nothing.
- Pending snapshots return commands in admission order with one consistent current mailbox cut.
- Existing SQLite files migrate without data loss; PostgreSQL migration is additive.
- Legacy mailbox rows, current approval/subagent consumers, `settled`, and rank replacement remain unchanged.

## Verification

```bash
uv run pytest tests/coding_agent/test_runtime_phase_b_uow.py tests/coding_agent/test_harness_p2_fact_source.py -q
uv run pytest tests/coding_agent/test_sqlite_local_durable_fencing.py tests/coding_agent/test_pg_durable_fencing.py tests/coding_agent/test_harness_p2_wrap.py tests/coding_agent/test_harness_p2_fact_source.py -q
uv run pytest tests/agentkit/ tests/coding_agent/ -q
uv run ruff check <changed-python-files>
uv run ruff format --check <changed-python-files>
```

## Loop policy

One bounded P1/P2 review, one accepted-fix pass, one verifier retest. Stop if the implementation requires a Phase C signature change or any D3b live activation.
