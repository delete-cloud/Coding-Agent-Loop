# ADR-0033: Add PostgreSQL tape debug queries

**Status**: Proposed
**Date**: 2026-05-19

## Context

Durable runtime replay records can explain run-level events and message
snapshots, but debugging a turn often still needs direct access to the tape
timeline. PostgreSQL tape storage already persists append-only entries in
`agent_tapes`, yet it only exposes save/load/list/truncate operations.

The active durable runtime objective requires `tape.info` and `tape.search` for
PostgreSQL tape storage with filters for `kind`, `run_id`, `tool_call_id`, and
`anchor_type`. This should remain a PG debug capability and must not change the
JSONL tape default or force every `TapeStore` implementation to implement debug
search.

## Decision

Add an optional `TapeDebugStore` protocol with two methods:

- `info(tape_id)` returns lightweight tape metadata or `None` when the tape has
  no rows.
- `search(...)` returns ordered tape rows with `tape_id`, `seq`, and decoded
  entry payloads, filtered by optional `tape_id`, `kind`, `run_id`,
  `tool_call_id`, and `anchor_type`.

Implement this protocol on `PGTapeStore` only. `TapeStore` remains the narrow
save/load/list/truncate contract, so JSONL and in-memory tape stores stay
compatible without adding debug indexes.

Filter extraction follows current entry shapes:

- `kind`: top-level `entry.kind`.
- `run_id`: `entry.meta.run_id` or `entry.payload.run_id`.
- `tool_call_id`: `entry.meta.tool_call_id` or `entry.payload.tool_call_id`.
- `anchor_type`: top-level `entry.anchor_type` or `entry.meta.anchor_type`.

`search` validates `limit > 0`, orders results by `tape_id, seq`, and caps rows
with SQL `LIMIT`. It does not perform full-text search, inspect raw prompt
content, or alter tape persistence schema.

## Alternatives Rejected

- Add debug methods to `TapeStore` directly - rejected because JSONL defaults
  should not grow optional PG-only query requirements.
- Add a new indexed tape table - rejected because the required filters can be
  derived from existing JSONB entries and no destructive migration is needed.
- Return raw database rows - rejected because callers should receive typed
  Python records with decoded entry dictionaries.
- Add HTTP endpoints now - rejected because G09 only requires the PG tape
  storage capability; public smoke/docs can cover tape debug in G11.

## Acceptance Criteria

- [x] `test_info_returns_tape_metadata`
- [x] `test_info_returns_none_for_missing_tape`
- [x] `test_search_filters_by_kind_run_tool_call_and_anchor_type`
- [x] `test_search_rejects_non_positive_limit`
- [x] `uv run pytest tests/agentkit/storage/test_pg.py -k "tape" -v`
- [x] `uv run pytest tests/agentkit/storage/test_protocols.py -v`
- [x] `uv run ruff check src/agentkit/storage/pg.py src/agentkit/storage/protocols.py src/agentkit/storage/__init__.py tests/agentkit/storage/test_pg.py`
- [x] `uv run ruff format --check src/agentkit/storage/pg.py src/agentkit/storage/protocols.py src/agentkit/storage/__init__.py tests/agentkit/storage/test_pg.py`

## References

- `docs/adr/0029-durable-runtime-identity.md`
- `docs/adr/0030-postgresql-durable-runtime-store.md`
- `docs/durable_runtime/GOAL_PROGRESS.md`
- `src/agentkit/storage/pg.py`
- `src/agentkit/storage/protocols.py`
- `tests/agentkit/storage/test_pg.py`
