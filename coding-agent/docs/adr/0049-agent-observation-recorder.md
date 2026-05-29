# ADR-0049: Agent Observation Recorder

**Status**: Accepted
**Date**: 2026-05-29

## Context

Agent observability needs both realtime failure localization and readable
turn-level summaries. A turn-end-only projection from tape is useful for
Langfuse input/output, but it loses the last known action if the service exits
before the turn completes.

The raw tape remains the execution fact source and may contain prompts, tool
arguments, command output, and secrets. Shared tracing backends must receive only
sanitized projections.

## Decision

Add a Coding Agent product-layer `AgentObservationRecorder` that is scoped to a
session turn. `SessionManager` creates it before `adapter.run_turn(...)`, and the
adapter records sanitized LLM usage, tool-call, and tool-result events as they
happen.

Persist realtime observations to local append-only JSONL under
`data/observability/runs/<run_id>/observations.jsonl`. Export Langfuse
`agent.turn.sanitized` spans only from sanitized turn projections; raw tape stays
local and referenced by run/session ids.

OTLP may export `langfuse.observation.input` and
`langfuse.observation.output` only when the JSON payload matches the sanitized
projection schema. This keeps Langfuse useful without opening a generic raw
content egress path.

## Alternatives Rejected

- Turn-end-only projection — readable in Langfuse, but a mid-turn crash leaves no
  last-action evidence.
- Raw tape to Langfuse — useful for debugging, but violates the no-leak boundary
  for prompts, tool arguments, command output, and secrets.
- AgentKit Core tracing rewrite — too broad; the recorder belongs in the
  Coding Agent product layer and consumes existing runtime/adapter events.

## Acceptance Criteria

- [x] `test_recorder_persists_turn_started_before_turn_finishes`
- [x] `test_tool_events_persist_shape_without_raw_arguments_or_result`
- [x] `test_sanitized_turn_projection_excludes_raw_turn_content`
- [x] `test_otlp_sink_exports_sanitized_langfuse_observation_payload`
- [x] `test_run_agent_records_turn_started_before_adapter_finishes`
- [x] `uv run pytest tests/coding_agent/test_agent_observability.py tests/coding_agent/test_observability.py tests/ui/test_session_manager_runtime.py -q`
- [x] `uv run pytest tests/coding_agent/test_pipeline_adapter.py tests/coding_agent/test_release_observability_contract.py -q`

## References

- `src/coding_agent/agent_observability.py`
- `src/coding_agent/adapter.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/observability.py`
- `tests/coding_agent/test_agent_observability.py`
- `tests/ui/test_session_manager_runtime.py`
