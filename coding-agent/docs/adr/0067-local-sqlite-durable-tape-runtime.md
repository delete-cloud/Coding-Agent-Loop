# ADR-0067: Use SQLite-first durable tape and runtime stores for local ACP

**Status**: Superseded
**Date**: 2026-06-05

Superseded by ADR-0068. ADR-0067 captured the first local ACP SQLite
durability slice, but its filesystem session metadata, multi-database local
bundle, caller-provided fencing token, and boundary-only `_assert_owner` model
are no longer the target architecture for transactional durable fencing.

## Context

ACP `session/load` now replays durable display events, but local ACP still
starts its session manager with filesystem session metadata and JSONL runtime
events. Real stdio validation showed that durable run display events can
survive process restart while the result endpoint projection can still lose the
final answer. That is a source-of-truth problem: replayable event history,
runtime result projections, and local multi-process access are not yet using one
transactional local store.

The repository already has the storage pieces needed for a narrower fix.
`SQLiteTapeStore` persists append-only tape entries with indexes, and
`SQLiteRuntimeStore` persists runs, runtime events, message snapshots, and
interactions. PostgreSQL-backed session ownership already models owner leases
with fencing tokens for multi-instance HTTP sessions. Bub's tape store design
reinforces the same boundary: tape remains the logical append-only model, while
SQLite or SQLAlchemy are replaceable store backends.

This ADR scopes the next refactor to local ACP/local CLI durability. It must not
change cloud production semantics, require multiple pods to share a SQLite file,
or replace tape with mutable result tables.

## Decision

Make local ACP/local CLI storage SQLite-first while preserving tape as the
authoritative append-only logical model.

The local default for ACP should move from JSONL runtime storage to a coherent
SQLite-backed bundle:

- filesystem session metadata for local session records unless a later ADR
  replaces local session metadata storage;
- `SQLiteTapeStore` for authoritative tape entries;
- `SQLiteCheckpointStore` for local checkpoints;
- `SQLiteRuntimeStore` for runs, runtime events, interactions, and message
  snapshots.

`session/list`, `session/load`, `session/result`, and ACP replay must be derived
from durable store state. Result records, display event replay, message
snapshots, topic views, and last-message summaries are projections or
checkpoints. They must remain reconstructible from tape plus durable runtime
metadata and must not become independent sources of truth.

JSONL remains available only for legacy import, export, debugging, and a
short-term compatibility window. Do not keep JSONL and SQLite as dual
authoritative stores for the same local session.

Add a local SQLite implementation of the existing `SessionOwnerStoreProtocol`
for ACP/local multi-process ownership. The owner record must include
`session_id`, `owner_id`, `lease_expires_at`, and a monotonic fencing token.
Mutation paths that already call `SessionManager._assert_owner` continue to use
that boundary. When a lease expires, takeover must advance the fencing token so
a suspended old owner cannot resume and append or mutate state with stale
authority.

Cloud/server production remains PostgreSQL-first for multi-instance durability.
SQLite is a single-host local store. Do not use a shared SQLite file as the
cloud multi-pod coordination mechanism. Future cloud changes should keep the
same repository/store interfaces and use PostgreSQL or another server database.

Implementation order:

1. Add this ADR and contract tests that describe the SQLite-first local ACP
   behavior.
2. Add `SQLiteSessionOwnerStore` under the existing session owner store
   protocol and run the current owner/fencing tests against it.
3. Switch ACP/local CLI construction to configure SQLite tape, checkpoint, and
   runtime stores, plus SQLite owner leases.
4. Make ACP `session/list`, `session/load`, and result queries rely on durable
   store replay/projections after process restart.
5. Add JSONL import/export/backcompat tooling for existing local sessions.
6. Remove JSONL as an authoritative local ACP runtime path after compatibility
   tests and migration gates pass.

## Alternatives Rejected

- Keep JSONL as the local authoritative store — rejected because local
  multi-process recovery would require hand-written locking, indexes, replay
  repair, and ownership semantics that duplicate database behavior.
- Replace tape with mutable session/result tables — rejected because tape is
  the durable append-only conversation and event-history model. Result/list/load
  data are projections.
- Directly depend on Bub tapestore packages — rejected for now because this
  repository already has Python `SQLiteTapeStore` and PostgreSQL stores with
  product-specific indexing and runtime integration. Bub remains design
  guidance for keeping store backends behind a stable tape abstraction.
- Use SQLite for cloud multi-pod session ownership — rejected because SQLite is
  a local single-host store and should not be treated as a distributed
  coordination service.
- Add a second ACP-only lease system — rejected because `SessionManager` already
  has owner/fencing enforcement points. Local ACP should implement the existing
  protocol, not bypass it.

## Acceptance Criteria

- [ ] `test_acp_uses_sqlite_local_storage_bundle_by_default`
- [ ] `test_sqlite_session_owner_store_acquire_renew_release_and_takeover`
- [ ] `test_acp_restart_load_replays_final_answer_from_sqlite_runtime_store`
- [ ] `test_acp_concurrent_prompt_on_same_session_returns_busy_or_conflict`
- [ ] `test_stale_sqlite_owner_fencing_token_cannot_mutate_session`
- [ ] `test_jsonl_local_tape_import_preserves_sqlite_replay_projection`
- [ ] `uv run pytest tests/acp tests/cli/test_entrypoint_contract.py tests/ui/test_session_manager_owner_checks.py tests/coding_agent/test_sqlite_runtime_store.py tests/agentkit/storage/test_sqlite.py -v`

## References

- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0030-postgresql-durable-runtime-store.md`
- `docs/adr/0033-postgresql-tape-debug-queries.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0061-acp-stdio-adapter.md`
- `docs/adr/0063-acp-session-load-replay.md`
- `src/agentkit/storage/sqlite.py`
- `src/coding_agent/runtime_store.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/server/stores/session_owner_store.py`
- `src/coding_agent/cli/acp_command.py`
- `https://github.com/bubbuild/bub-contrib/tree/main/packages/bub-tapestore-sqlite`
- `https://github.com/bubbuild/bub-contrib/tree/main/packages/bub-tapestore-sqlalchemy`
