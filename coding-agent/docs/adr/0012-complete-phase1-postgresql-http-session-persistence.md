# ADR-0012: Complete Phase 1 PostgreSQL HTTP session persistence

**Status**: Proposed
**Date**: 2026-04-17

## Context

The repository already includes reusable PostgreSQL persistence primitives for session metadata and tapes in `agentkit.storage.pg`, and the HTTP/UI session stack already has stable tape and checkpoint semantics backed by tests. But the Phase 1 goal for HTTP session persistence is still incomplete on `main`.

`SessionManager` still defaults to the local JSONL tape store and filesystem checkpoint store, `http_server.py` still constructs `SessionManager()` directly with those defaults, and the UI session metadata layer still only provides in-memory and Redis-backed implementations. There is also no PostgreSQL `CheckpointStore` implementation in `agentkit`, which means the current HTTP checkpoint path cannot persist checkpoint snapshots/catalogs in PostgreSQL even when tape persistence uses PostgreSQL.

Phase 1 is still explicitly single-instance and keeps active runtime state (`task`, `approval_event`, `event_queues`, `runtime_pipeline`, `runtime_ctx`, `runtime_adapter`) local-authoritative. This ADR only closes the durable storage gap for restart-safe metadata, stable tape history, and checkpoint snapshots/catalogs.

## Decision

Complete Phase 1 by adding PostgreSQL-backed checkpoint persistence in `agentkit` and one config-driven app-layer persistence path for the HTTP/UI session stack.

Specifically:

- Add `PGCheckpointStore` to `agentkit.storage.pg` implementing the existing `CheckpointStore` protocol.
- Add a synchronous PostgreSQL-backed UI session metadata store in `coding_agent.ui.session_store` rather than widening `SessionManager` to the async `agentkit` session protocol.
- Add a small app-layer persistence factory in `coding_agent.ui` that builds the default session metadata store, tape store, checkpoint store, and shutdown hook from the existing flat `[storage]` config in `src/coding_agent/agent.toml`.
- Make both `SessionManager()` defaults and HTTP server startup use that same persistence factory instead of hard-coded JSONL/filesystem/in-memory defaults.
- Keep runtime execution ownership, approval routing, and event routing out of scope for this ADR and Phase 1.

## Alternatives Rejected

- Reuse the async `agentkit.PGSessionStore` directly inside `SessionManager` — rejected because it would widen a synchronous UI/session-manager boundary into a larger refactor.
- Keep checkpoints on the filesystem while only moving tapes to PostgreSQL — rejected because checkpoint restore belongs to the same stable tape timeline and Phase 1 needs durable checkpoint snapshots/catalogs.
- Add PostgreSQL support by sprinkling separate wiring logic across `SessionManager`, `http_server.py`, and REPL startup — rejected because Phase 1 needs one config-driven default path, not divergent per-entrypoint behavior.
- Expand into Phase 2 owner/lease/fencing work while touching persistence — rejected because Phase 1 remains single-instance and local-authoritative for active runtime state.

## Acceptance Criteria

- [ ] PostgreSQL checkpoint persistence is covered by tests in `tests/agentkit/storage/test_pg.py`, including round-trip snapshot persistence and ordered listing by tape.
- [ ] Config-driven persistence bundle construction from `[storage]` configuration is covered by tests in `tests/coding_agent/plugins/test_storage_factory.py`.
- [ ] `SessionManager` default persistence wiring is covered by tests in `tests/ui/test_session_manager_public_api.py` and `tests/ui/test_session_persistence.py`.
- [ ] HTTP server startup uses the configured persistence bundle for the global session manager, covered by tests in `tests/ui/test_http_server.py`.
- [ ] `uv run pytest tests/agentkit/storage/test_pg.py tests/coding_agent/plugins/test_storage_factory.py tests/ui/test_session_persistence.py tests/ui/test_session_manager_public_api.py tests/ui/test_http_server.py -v`

## References

- `src/agentkit/storage/pg.py`
- `src/agentkit/storage/protocols.py`
- `src/agentkit/checkpoint/service.py`
- `src/coding_agent/ui/session_store.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/agent.toml`
- `tests/agentkit/storage/test_pg.py`
- `tests/ui/test_session_persistence.py`
- `tests/ui/test_session_manager_public_api.py`
- `tests/ui/test_http_server.py`
