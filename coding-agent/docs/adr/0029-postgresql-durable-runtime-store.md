# ADR-0029: Add PostgreSQL durable runtime store

**Status**: Proposed
**Date**: 2026-05-19

## Context

Coding-Agent-Loop already has PostgreSQL-backed stores for selected session,
tape, checkpoint, owner, and workspace metadata. Runtime execution metadata is
still not represented as a durable, queryable control-plane store for agent
runs, runtime events, message snapshots, or human/agent interactions.

This slice needs a storage layer only. It must not change the default JSONL/file
storage path, widen `SessionManager`, or alter the `agentkit` runtime pipeline.
The store should be available for later integration while remaining inert until
an application layer explicitly constructs and uses it.

## Decision

Add an app-owned PostgreSQL durable runtime store in `coding_agent` that reuses
the existing `agentkit.storage.pg.PGPool` async style. The schema contains four
tables:

- `agent_runs` for run lifecycle metadata keyed by `run_id`.
- `runtime_events` for append-only runtime events keyed by idempotent
  `event_id` and replayed by database sequence.
- `run_message_snapshots` for latest serialized message state keyed by
  `snapshot_id`.
- `agent_interactions` for request/response interactions keyed by
  `interaction_id`, with single-shot resolution semantics.

Schema initialization is lazy and idempotent through `CREATE TABLE IF NOT
EXISTS`, matching the existing PostgreSQL store pattern. The first version keeps
records as lightweight dataclasses plus JSON-compatible metadata/payload maps.
It does not wire the store into `SessionManager`, HTTP startup, CLI startup,
JSONL defaults, or the AgentKit pipeline.

## Alternatives Rejected

- Put the store in `agentkit` immediately - rejected because the table set is a
  Coding-Agent control-plane persistence slice and current integration is
  intentionally app-side only.
- Integrate the store into `SessionManager` in the same change - rejected
  because this task is a durable store layer only and must preserve existing
  JSONL storage defaults.
- Reuse tape or checkpoint tables for runtime events and interactions -
  rejected because replayable runtime controls, message snapshots, and
  single-shot interaction resolution have different query and idempotency
  contracts.

## Acceptance Criteria

- [x] `test_create_update_load_and_list_agent_runs`
- [x] `test_append_runtime_event_replays_in_sequence_order`
- [x] `test_append_runtime_event_returns_existing_record_for_duplicate_event_id`
- [x] `test_save_load_and_list_run_message_snapshots`
- [x] `test_create_and_resolve_agent_interaction_idempotently`
- [x] `test_pg_runtime_store_schema_initialization_is_idempotent`
- [x] `uv run pytest tests/coding_agent/test_pg_runtime_store.py -v`

## References

- `src/agentkit/storage/pg.py`
- `src/coding_agent/ui/session_store.py`
- `src/coding_agent/ui/workspace_store.py`
- `tests/agentkit/storage/test_pg.py`
