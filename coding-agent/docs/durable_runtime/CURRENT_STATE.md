# Durable Runtime Current State

G00 inspection date: 2026-05-18

This document records the current durable-runtime-adjacent behavior before
adding new runtime persistence. It is intentionally descriptive only; no
production code changes are part of G00.

## Scope Inspected

- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/adapter.py`
- `src/coding_agent/plugins/storage.py`
- `src/agentkit/storage/pg.py`
- `src/coding_agent/observability.py`
- `src/agentkit/observability/*`
- `src/coding_agent/approval/*`
- `src/agentkit/runtime/messages.py`
- `src/coding_agent/wire/*`
- `src/coding_agent/ui/http_server.py`
- `src/agentkit/tape/*`

## SessionManager

`SessionManager` is the application-level owner of HTTP session lifecycle,
workspace binding, runtime construction, turn serialization, approval
coordination, checkpoint restore/capture, and SSE fan-out.

Durable session metadata is stored through `coding_agent.ui.session_store`.
`Session.to_store_data()` persists stable session properties including
`id`, timestamps, `repo_path`, `origin`, `execution_binding`, provider/model
settings, `max_steps`, `tape_id`, and `last_failure_details`. It does not
persist active turn state, runtime messages, wire events, or pending approval
internals except for projected session metadata such as `pending_approval`
during saves.

Runtime state is process-local:

- `Session.runtime_pipeline`, `runtime_ctx`, and `runtime_adapter` are cached in
  memory.
- `Session.runtime_message_bus` defaults to `InMemoryRuntimeMessageBus`.
- `Session.approval_decision_cursor` is in memory.
- `Session.event_queues` are in memory.
- `Session.task`, `turn_in_progress`, `turn_status`, and `current_turn_id` are
  process-local turn state, though `run_agent()` persists session metadata after
  mutating some of these fields.

Turn execution is serialized by `_turn_lock_for(session_id)`. `run_agent()`
sets `turn_status = "running"`, assigns a new `current_turn_id`, builds or
reuses a pipeline/adapter, restores tape by `session.tape_id`, runs
`PipelineAdapter.run_turn(prompt)`, then clears `turn_in_progress` and returns
`turn_status` to `idle` unless the turn failed. No durable `run_id` is created
or passed into agent creation at this layer today.

Cancellation is represented through `turn_status` values
`idle | running | cancelling | cancelled | failed`. There is no durable
queued/completed/cancelled/interrupted run lifecycle table.

## PipelineAdapter

`PipelineAdapter` is the bridge from the internal legacy
`agentkit.runtime.pipeline.Pipeline` provider events to `coding_agent.wire`
messages.

Key behavior:

- Appends the user prompt as a tape `Entry(kind="message")`.
- Assigns `ctx.on_event = self._handle_event`.
- Converts `TextEvent`, `ThinkingEvent`, `UsageEvent`, `ToolCallEvent`,
  `ToolResultEvent`, and `DoneEvent` into wire messages through a configured
  consumer.
- Uses display redaction for tool result display values.
- Returns a `TurnOutcome` with `StopReason`, final message, step count, and
  error text.

The adapter does not persist runtime events or message snapshots. It forwards
events to its `WireConsumer` if present, otherwise drops UI event emission.

## Storage Plugins And Defaults

`coding_agent.plugins.storage.StoragePlugin` provides tape/session storage to
plugin composition.

Defaults:

- Tape backend defaults to `jsonl`.
- Session backend defaults to file when tape backend is `jsonl`.
- PG tape/session storage is opt-in through config or injected pool.
- `JSONLTapeStore` appends entries to `data/tapes/<tape_id>.jsonl`.

PG behavior:

- `StoragePlugin._load_pg_types()` dynamically imports `PGPool`,
  `PGSessionLock`, `PGSessionStore`, and `PGTapeStore`.
- For `backend="pg"`, `StoragePlugin` creates `PGTapeStore` and a
  `PGSessionLock`.
- SessionManager has its own storage factory path for HTTP session metadata,
  tape store, and checkpoint store. If configured `tape_backend` is `pg`, it
  uses `PGTapeStore`; otherwise it uses JSONL. If configured checkpoint backend
  is `pg`, it uses `PGCheckpointStore`; otherwise filesystem checkpoints.

There is no runtime-store plugin or composition output for `agent_runs`,
`runtime_events`, `run_message_snapshots`, or `agent_interactions`.

## Existing PG Storage

`agentkit.storage.pg` owns generic PG-backed stores and the shared `PGPool`
style.

Current tables:

- `agent_sessions` via `PGSessionStore`.
- `agent_tapes` via `PGTapeStore`.
- `agent_checkpoints` via `PGCheckpointStore`.
- `session_owners` via `PGSessionOwnerStore`.

Common style:

- `PGPool` lazily creates one asyncpg pool and registers `json`/`jsonb` codecs.
- Store classes keep `_schema_ready` and lazily run
  `CREATE TABLE IF NOT EXISTS ...` on first use.
- Store methods pass Python JSON-compatible objects to asyncpg with `::jsonb`
  casts.
- Row parsing validates expected field types and fails fast on malformed rows.

`PGTapeStore` is append-only by `(tape_id, seq)` and has load/list/truncate.
It does not expose metadata search, `run_id`, `tool_call_id`, or anchor filters.

## Observability

AgentKit defines provider-neutral observability primitives:

- `SpanRecord`
- `ObservationEvent`
- `ObservationSink`
- `NoopObservationSink`
- `record_span(...)`

`agentkit.runtime.pipeline` emits span attributes for stage spans and LLM
generation with `session_id` and `run_id` when `ctx.run_context` exists.
`PipelineContext` can carry `run_context`, `runtime_message_bus`, and
`runtime_message_cursor`.

`coding_agent.observability` owns OTLP/Langfuse export configuration. Defaults
are disabled. The exporter:

- Builds OTLP HTTP payloads.
- Derives trace id from `session_id:run_id` when both exist.
- Adds resource attributes `session.id` and `run.id` when present.
- Drops attributes whose keys contain `content`, `message`, `prompt`, `result`,
  `secret`, or `text`.
- Fails open; exporter failures do not break runtime behavior.

Current correlation is limited. There is no consistent `run_id` from
`SessionManager.run_agent()`, and attributes such as `tape_id`, `tool_call_id`,
`interaction_id`, `event_id`, or `checkpoint_id` are not uniformly attached.

## Approval Flow

Approval is currently process-local plus runtime-message-backed decisions.

Components:

- `ApprovalStore` keeps pending requests in memory and resolves them through an
  `asyncio.Event`.
- `ApprovalCoordinator` manages request ordering and session-scoped approvals.
- `ApprovalDecisionConsumer` consumes `approval_decision` runtime messages and
  applies them to the in-memory coordinator.
- `SessionManager.submit_approval_response()` publishes an
  `approval_decision:<session_id>:<request_id>` message to the session runtime
  bus and consumes it idempotently.

Approval requests are projected into session metadata for UI visibility, but
pending requests and responses are not stored durably as interaction records.
After process restart, the runtime bus, approval cursor, pending request event,
and coordinator state are lost.

## Event And Wire Flow

`coding_agent.wire.protocol` defines typed wire messages used by CLI and HTTP
UI paths:

- `StreamDelta`
- `ThinkingDelta`
- `TurnStatusDelta`
- `ToolCallDelta`
- `ToolResultDelta`
- `ApprovalRequest`
- `ApprovalResponse`
- `TurnEnd`

`LocalWire` is an in-memory queue with outgoing and incoming queues. It does
not persist messages.

HTTP event flow:

- `/sessions/{session_id}/prompt` starts `SessionManager.run_agent()` as a task.
- `stream_wire_messages()` drains the session `LocalWire` outgoing queue and
  converts messages to SSE events.
- Each streamed event is also broadcast to active `/events` subscribers through
  `Session.event_queues`.
- `/sessions/{session_id}/events` is a fan-out stream over in-memory queues and
  sends keepalive pings.

There is no replay endpoint for completed events. There is no durable
`last_event_id` or sequence-backed filtering. HTTP SSE payloads currently
include some raw event content by design; tracing must not reuse those raw
fields as span attributes.

## Tape Flow

AgentKit tape is append-only in memory during a turn and persisted through the
configured tape store.

Core pieces:

- `Tape` owns entries, `tape_id`, optional `parent_id`, and `window_start`.
- `Entry` stores `id`, `kind`, `payload`, `timestamp`, and `meta`.
- `Anchor` is an `Entry` subtype with `anchor_type` and `source_ids`.
- `ForkTapeStore` writes only the delta from a fork back to the stable base
  `tape_id`.

JSONL tape storage remains the default. PG tape storage stores opaque entry
JSON by `tape_id` and `seq`. There is no current `tape.info` or `tape.search`
surface, and PG tape rows do not expose indexed fields for `kind`, `run_id`,
`tool_call_id`, or `anchor_type`.

## Current Durable Runtime Gaps

- No app-owned durable runtime store for runs, events, message snapshots, or
  interactions.
- No durable run lifecycle integrated into `SessionManager`.
- No stable `run_id` generated by HTTP turn execution.
- `current_turn_id` exists for compatibility but is not tied to a durable run.
- Runtime event flow is in-memory and not replayable after restart.
- Approval interactions are in-memory and not recoverable.
- Langfuse/OTLP correlation can use `run_id` only when a caller supplies an
  `AgentRunContext`; the HTTP session path does not yet do that.
- PG tape storage is append-only but lacks query/debug indexes.
- Stale running turns after process death are not recovered or marked.

## Constraints For Later Checkpoints

- Keep AgentKit core generic. App-specific durable runtime tables should start
  in `coding_agent`.
- Preserve JSONL/file defaults unless PG is explicitly configured.
- Do not rewrite the internal legacy `agentkit.runtime.pipeline.Pipeline`;
  add wrappers/adapters around existing extension points where possible.
- Avoid destructive migrations. New tables/indexes should be additive and
  idempotent.
- Do not put raw prompt, content, message, result, secret, or text values into
  tracing attributes.
