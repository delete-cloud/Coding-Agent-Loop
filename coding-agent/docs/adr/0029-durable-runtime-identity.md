# ADR-0029: Define durable runtime identity and correlation

**Status**: Proposed
**Date**: 2026-05-18
**Decision owner**: repository maintainer / current product owner

## Context

G00 documented that `SessionManager` has durable session metadata, a stable
`tape_id`, checkpoint metadata, process-local turn state, in-memory runtime
messages, and in-memory approval state. It also documented that the HTTP
runtime path does not currently create a stable durable `run_id`, even though
AgentKit already has `AgentRunContext.run_id` and observability can group spans
by `session_id:run_id` when that context is supplied.

The durable runtime phase needs a small identity model before adding stores,
SessionManager wiring, replay APIs, durable approval, observability enrichment,
and tape debug search. Without a shared identity contract, later checkpoints
would risk treating `current_turn_id`, `run_id`, tape ids, event ids, and
approval request ids as interchangeable even though they have different
lifetimes and compatibility constraints.

This ADR defines identity semantics only. It does not add production code,
schema, HTTP endpoints, or migrations.

## Decision

Use `session_id` as the existing stable user/session boundary and introduce
`run_id` as the durable identity for one execution attempt. Preserve
`current_turn_id`/`turn_id` as a compatibility field for existing HTTP and wire
contracts, but treat it as an alias/projection of the active root run once
durable run lifecycle wiring exists.

### Identity Fields

`session_id`

- Existing stable session identifier.
- Owns session metadata, workspace binding, owner lease checks, event stream
  authorization, and session-scoped listing.
- Must remain compatible with current HTTP routes and stored session metadata.

`run_id`

- New durable identity for one execution attempt in a session.
- Root user turns, retry attempts, setup/agent/finalize phases, and child agent
  runs may each have their own run record when later checkpoints need that
  detail.
- Root HTTP prompt execution creates one root `run_id`.
- Child/subagent runs derive from the parent through `parent_run_id` rather
  than overloading `session_id`.
- `run_id` is the primary trace grouping key with `session_id`.

`turn_id` / `current_turn_id`

- Compatibility projection for existing HTTP/SSE clients.
- During the transition, `Session.current_turn_id` should be set to the root
  `run_id` unless an older caller explicitly depends on an existing turn id.
- New durable storage and replay APIs should expose `run_id` first and may
  include `turn_id` as an alias for compatibility.
- Do not create a second independent durable lifecycle keyed only by
  `turn_id`.

`tape_id`

- Stable tape timeline identifier for a session/runtime context.
- Already persists through session metadata and checkpoint metadata.
- Run records should reference the active `tape_id` when known, but the tape
  remains append-only and is not replaced by run storage.
- Tape debug/search should index entry metadata around the existing tape rows
  rather than treating runtime runs as a tape substitute.

`event_id`

- Idempotency key for a persisted runtime event.
- A runtime event store should also assign a monotonic database sequence for
  replay and `last_event_id`/cursor filtering.
- Event ids should be stable across retry of the same event write. The replay
  sequence, not lexical `event_id`, defines event ordering.

`interaction_id`

- Idempotency key for a human/agent interaction such as HITL approval.
- Approval `request_id` should map to or be carried by `interaction_id` so
  duplicate responses resolve the same interaction once.
- Interactions can include request/response payload metadata, but tracing must
  not copy raw prompt/content/message/result/secret/text fields into span
  attributes.

`checkpoint_id`

- Existing checkpoint snapshot identifier.
- Checkpoints remain owned by checkpoint storage and refer to `tape_id`.
- Runtime records and spans may carry `checkpoint_id` when a run captures or
  restores a checkpoint, but checkpoint storage is not replaced by runtime
  storage.

### Langfuse / OTLP Correlation

The default trace grouping for a turn is `session_id + run_id`. Coding Agent's
OTLP adapter already derives a deterministic trace id from those attributes
when both are present. Later checkpoints should make the HTTP runtime path
populate those attributes consistently.

Allowed low-cardinality correlation attributes:

- `session_id`
- `run_id`
- `parent_run_id`
- `turn_id`
- `tape_id`
- `tool_call_id`
- `interaction_id`
- `event_id`
- `checkpoint_id`
- provider/model identifiers when already allowed by observability policy

Disallowed tracing attributes:

- raw prompt text
- message content
- tool result content
- secrets, tokens, credentials, environment values, or unredacted command output

The exporter must continue applying the existing denylist for keys containing
`content`, `message`, `prompt`, `result`, `secret`, or `text`. Future work can
add stricter allowlists, but must not weaken the current privacy behavior.

### Boundary

AgentKit may continue to own provider-neutral `AgentRunContext` and span
primitives. Coding Agent owns durable runtime control-plane records, HTTP/SSE
compatibility, approval interaction persistence, replay APIs, and OTLP/Langfuse
product policy.

## Alternatives Rejected

- Use `current_turn_id` as the durable primary key - rejected because it is a
  compatibility field on `Session`, not a complete run lifecycle identity, and
  it cannot naturally represent child runs or retry attempts.
- Use only `tape_id` for runtime identity - rejected because one tape can span
  many turns/runs and replay/debug queries need run and event boundaries.
- Use only provider trace ids - rejected because durable runtime behavior must
  work when observability is disabled or exporter delivery fails.
- Move all identity and durable store policy into AgentKit - rejected because
  HTTP routes, session ownership, approval interactions, and Langfuse/OTLP
  privacy policy are Coding Agent product concerns.

## Acceptance Criteria

Implementation of this ADR is complete when later checkpoints add deterministic
tests covering:

- [ ] `test_session_manager_creates_run_id_and_preserves_current_turn_id_alias`
- [ ] `test_run_context_receives_session_run_and_tape_identity`
- [ ] `test_runtime_events_replay_by_sequence_after_last_event_id`
- [ ] `test_approval_request_maps_to_idempotent_interaction_id`
- [ ] `test_otlp_trace_id_uses_session_id_and_run_id`
- [ ] `test_observability_drops_raw_prompt_message_result_secret_text_attributes`
- [ ] `uv run pytest tests/ui tests/coding_agent tests/agentkit/runtime -k "run_id or current_turn_id or last_event_id or interaction_id or otlp" -v`

G01 itself is documentation-only and is complete when this ADR is merged with
`docs/durable_runtime/GOAL_PROGRESS.md` updated.

## References

- `docs/durable_runtime/CURRENT_STATE.md`
- `docs/adr/0028-observability-and-langfuse-integration.md`
- `src/agentkit/runtime/context.py`
- `src/agentkit/runtime/pipeline.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/observability.py`
- `src/coding_agent/wire/protocol.py`
- `src/coding_agent/approval/runtime_messages.py`
