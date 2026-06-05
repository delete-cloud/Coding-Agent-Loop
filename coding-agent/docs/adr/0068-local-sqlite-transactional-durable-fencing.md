# ADR-0068: Local SQLite transactional durable fencing

**Status**: Proposed
**Date**: 2026-06-05

Supersedes ADR-0067. ADR-0067 remains the record of the first ACP SQLite
durability slice, but it is no longer the target architecture for local
transactional durability and fencing.

## Context

PR #556 made local ACP use SQLite tape, checkpoint, and runtime stores, added a
SQLite session owner store, acquired owner leases on ACP load/resume, renewed
local ACP owner leases, and cancelled active local turns after owner loss. That
was a correct first slice, but it still uses separate local durability
boundaries: filesystem session metadata, SQLite tape/runtime/checkpoint files,
and a separate SQLite session owner file.

That shape is not enough for durable fencing. A boundary-level
`SessionManager._assert_owner` or cross-database owner preflight check can reject
many stale operations, but it has a time-of-check/time-of-use gap. If the owner
row and the protected write live in different database transactions, the owner
can lose its lease after the check and still mutate tape, runtime, checkpoint,
or session metadata state.

The next refactor therefore needs a data-model decision before changing more
defaults. The local store layout, owner epoch allocation, target ownership
checks, worker authority, maintenance authority, and PG/cloud extension point
must be defined before local defaults move further toward SQLite.

## Decision

Adopt a single local SQLite database as the target architecture for local
durable execution state. The local database is named `local.sqlite3` and is the
target home for session owners, session metadata, tape, runtime, and checkpoint
tables.

Durable fencing means that the owner epoch check, target ownership check, and
protected mutation happen in the same transaction. A write path is durably
fenced only if the store transaction verifies all of these facts before the
mutation commits:

- owner row exists for the session;
- owner epoch matches the caller authority;
- owner lease is live;
- mutation target belongs to the same session;
- protected mutation is executed before the transaction commits.

Cross-database `_assert_owner` checks and owner preflight checks remain useful
admission or user-facing validation tools, but they are best-effort preflight.
Best-effort preflight does not provide durable fencing and must not be
documented as preventing stale writes after owner loss.

### Local layout

The target local layout is a single local SQLite database:

- `session_owners`
- session metadata tables
- tape entry and memory-record tables
- runtime run, event, message snapshot, and interaction tables
- checkpoint tables

SQLite is a single-writer local store. That is acceptable for local ACP, local
daemon, local REPL, and single-host development use, but transactions must stay
short. Protected transactions must not include LLM calls, tool execution,
workspace cleanup, runtime close, external worker network calls, or other
long-running side effects. Configure a bounded busy timeout and define
`SQLITE_BUSY` as a retryable or surfaced contention error at the call boundary,
not as silent data loss.

### Owner epoch

Replace caller-authored fencing tokens with a DB-managed owner epoch for the
local transactional store. Acquire and takeover allocate or advance the epoch in
the database transaction that changes session ownership. Renew does not advance
the epoch; renew only extends the live lease for the current owner and epoch.
Takeover advances the epoch. A stale caller with an older epoch cannot pass a
protected write.

### Target ownership

Protected writes must prove that the target being mutated belongs to the same
session as the owner authority. It is not sufficient to validate the caller's
`session_id` and then mutate an arbitrary target id.

The target ownership rules are:

- run-scoped writes must join or look up `agent_runs.run_id -> session_id` in
  the same transaction and match the caller session id;
- interaction, snapshot, and event writes must prove ownership through their
  run id and session id chain;
- tape writes must prove the `tape_id` is the stable tape for that session;
- checkpoint writes, deletes, and restores must prove `checkpoint_id` and
  `tape_id` belong to that session;
- upserts must not rebind an existing `run_id`, `snapshot_id`,
  `interaction_id`, `checkpoint_id`, or `tape_id` to a different session.

Contract tests must include a mismatch case: the owner of session A cannot
mutate a run, checkpoint, tape, interaction, snapshot, or event target that
belongs to session B.

### Authority modes

Normal owner-scoped writes must fence owner epoch, live lease, target ownership,
and the mutation in one transaction.

Worker authority is separate but subordinate to session takeover. Attached and
external worker claim, heartbeat, append event, finalize, cancel, and recovery
paths must either bind to the session owner epoch in the same transaction or use
a distinct worker lease and claim token fence that cannot bypass session
takeover. A worker claim token cannot authorize a write to a different session,
cannot write after its claim expires, and cannot write final status after
session takeover unless the worker authority model explicitly says the new
owner has delegated that write.

Maintenance authority is for repair, cleanup, and recovery. It requires a
maintenance lock or an expired/no-active-owner condition, must write a
repair/audit event when it changes durable state, and must be idempotent.
Maintenance writes are the path for stale-owner interrupted state when an old
owner lost its epoch and cannot write through the normal path.

Offline migration, import, and export run with the service stopped or under an
exclusive file lock. They do not use normal owner-scoped authority and must not
run concurrently with local ACP, daemon, REPL, or worker writers.

### Write-path inventory

The protected write inventory includes at least:

- tape save;
- tape truncate;
- memory append and memory replace;
- checkpoint save;
- checkpoint delete;
- checkpoint restore;
- runtime create;
- runtime update;
- claim attached worker;
- claim external worker;
- worker heartbeat;
- worker finalize;
- worker cancel;
- worker recovery;
- runtime append event;
- save message snapshot;
- create interaction;
- resolve interaction;
- session metadata save;
- session metadata delete;
- turn state updates;
- runtime config updates;
- MCP servers updates;
- additional directories updates;
- session close;
- session shutdown.

Checkpoint restore is a high-risk combined mutation. It must be represented as
one atomic protected operation where possible, or decomposed only with an
explicit recovery contract that can repair partially applied restore work.

### Error and cleanup semantics

Owner loss cancellation is mitigation, not durable fencing. It should stop local
work quickly and reduce further side effects, but it is not the final protection
against stale writes.

After epoch loss, the old owner cannot write cancelled, failed, result, or
session metadata terminal state through the normal write path. That terminal or
interrupted state must be written by the new owner or a reconciler using
maintenance authority, unless the old owner still passes the same transactional
fencing rules as any other writer.

In particular, the old owner cannot write cancelled and the old owner cannot
write failed after epoch loss unless those writes pass the normal transactional
fencing contract.

### PG and cloud parity

Local implementation may land first, but the abstraction must not be
SQLite-only. PostgreSQL/cloud can provide the same contract by checking the
owner row condition, target ownership, and protected mutation in one PG
transaction. PG/cloud parity can land after local SQLite as a separate PR, but
interfaces introduced for local fencing must leave room for a PG transaction
implementation.

## Implementation Plan

PR 1 is this ADR, the write-path inventory, and contract/design tests only. It
does not change product defaults, store APIs, or runtime write behavior.

PR 2 wires the local/default SQLite bundle after this layout is settled. The
preferred default is fully local SQLite: session metadata, owner, tape, runtime,
and checkpoint in one `local.sqlite3`. If risk forces filesystem session
metadata retention, the PR must call that partial SQLite and must not claim
fully SQLite or durable fencing. PR 2 must cover ACP, REPL, run, daemon,
daemon REPL, daemon run, TUI, HTTP local mode, storage plugin defaults, docs and
config examples, JSONL/FS read-only fallback or migration command, schema
auto-init, and migration tests.

PR 3 implements transactional durable fencing. It introduces protected mutation
APIs that carry session id and owner epoch, verify owner liveness and target
ownership, and commit protected writes in one transaction. It covers the
write-path inventory and adds maintenance/reconciler authority without ad hoc
`skip_fencing=True` bypasses.

PR 4 adds PG/cloud parity. It is not required to block the local SQLite
implementation, but the PR 3 interfaces must already support a PG transaction
implementation.

## Alternatives Rejected

- Keep ADR-0067 as the target architecture — rejected because filesystem
  session metadata, multiple SQLite databases, caller-provided fencing tokens,
  and boundary-only `_assert_owner` checks are insufficient for transactional
  durable fencing.
- Use SQLite `ATTACH` as the durable fencing foundation — rejected. Multi-file
  transactions with attached databases have caveats: the main database must not
  be `:memory:` and journal mode is not WAL for cross-file crash atomicity.
  Current stores use independent connections, so `ATTACH` would also require a
  broad connection and transaction redesign. Single local database is simpler
  and gives clearer atomicity.
- Treat owner preflight as durable fencing — rejected because it does not
  protect against owner loss between check and mutation.
- Let workers bypass session ownership with claim tokens alone — rejected
  because a worker claim token must not become a stale-owner write path after
  session takeover.
- Add `skip_fencing=True` for repair — rejected because repair authority needs
  explicit maintenance lock or no-active-owner conditions, auditability, and
  idempotent semantics.
- Require PG/cloud parity in the first local implementation PR — rejected
  because local SQLite can land first, provided the contract and interfaces do
  not exclude PG transaction support.

## Acceptance Criteria

- [ ] `test_adr_0068_supersedes_adr_0067_without_conflicting_target_architecture`
- [ ] `test_adr_0068_defines_single_db_epoch_and_transactional_fencing_contract`
- [ ] `test_adr_0068_rejects_attach_as_the_durable_fencing_foundation`
- [ ] `test_adr_0068_requires_same_session_target_ownership_for_protected_writes`
- [ ] `test_adr_0068_covers_worker_maintenance_and_cleanup_authority`
- [ ] `test_adr_0068_records_full_write_path_inventory_and_future_pr_sequence`
- [ ] Future PR 3: owner of session A cannot mutate run, checkpoint, tape,
  interaction, snapshot, or event target of session B.
- [ ] Future PR 3: A acquires epoch N, A writes own session target, and the
  write succeeds.
- [ ] Future PR 3: A lease expires, B takes over with epoch N+1, A write fails,
  and B write succeeds.
- [ ] Future PR 3: A renews and keeps epoch N, and A write succeeds.
- [ ] Future PR 3: expired same owner without live lease cannot write until the
  reacquire/takeover policy grants a live epoch.
- [ ] Future PR 3: stale owner cancellation cleanup cannot persist final
  status, result, or session metadata through the normal write path.
- [ ] Future PR 3: worker claim, heartbeat, and finalize cannot bypass session
  takeover or target ownership rules.
- [ ] Future PR 3: checkpoint restore is atomic or has an explicit recovery
  contract.
- [ ] `uv run pytest tests/coding_agent/test_durable_fencing_adr_contract.py -q`

## References

- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0030-postgresql-durable-runtime-store.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0067-local-sqlite-durable-tape-runtime.md`
- `src/agentkit/storage/sqlite.py`
- `src/agentkit/storage/pg.py`
- `src/coding_agent/runtime_store.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/server/stores/session_store.py`
- `src/coding_agent/server/stores/session_owner_store.py`
- `tests/coding_agent/test_durable_fencing_adr_contract.py`
- `https://www.sqlite.org/lang_attach.html`
- `https://www.sqlite.org/wal.html`
