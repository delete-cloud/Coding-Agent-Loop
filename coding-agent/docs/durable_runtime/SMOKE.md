# Durable Runtime Smoke Coverage

This document records the deterministic smoke layer for the durable runtime
objective. It is intentionally local-only: no real LLM, PostgreSQL server,
Langfuse project, network credential, scheduler, or destructive migration is
required.

## Scenarios

| Scenario | Smoke proof | Command |
| --- | --- | --- |
| normal run | `SessionManager.run_agent()` creates a queued run, marks it running and completed, persists wire events, and writes the latest message snapshot. | `uv run pytest tests/integration/test_durable_runtime_smoke.py -k "normal_and_failed" -v` |
| failed run | `SessionManager.run_agent()` records an adapter error outcome as a failed durable run with error/result metadata and a snapshot. | `uv run pytest tests/integration/test_durable_runtime_smoke.py -k "normal_and_failed" -v` |
| approval run | A runtime approval request persists a pending interaction, records the approval request as a runtime event, resolves the interaction after an approval decision, and completes the run. | `uv run pytest tests/integration/test_durable_runtime_smoke.py -k "approval_run" -v` |
| runtime replay | HTTP replay endpoints return a visible run, latest message snapshot, and events filtered after `last_event_id`. | `uv run pytest tests/integration/test_durable_runtime_smoke.py -k "runtime_replay" -v` |
| Langfuse/OTLP correlation | The OTLP exporter posts to a Langfuse OTLP trace endpoint, groups by `session_id` and `run_id`, exports safe runtime ids, and drops raw prompt/message/result/secret/text attributes. | `uv run pytest tests/integration/test_durable_runtime_smoke.py -k "langfuse_otlp" -v` |
| tape debug | `PGTapeStore.info()` and `PGTapeStore.search()` return tape metadata and filter entries by `run_id`, `tool_call_id`, and `anchor_type`. | `uv run pytest tests/integration/test_durable_runtime_smoke.py -k "tape_debug" -v` |

## Full Command

```bash
uv run pytest tests/integration/test_durable_runtime_smoke.py -v
```

## Supporting Regression Commands

```bash
uv run pytest tests/ui/test_session_manager_runtime.py -k "agent_run or persists_wire_events or approval_interaction or message_snapshot" -v
uv run pytest tests/ui/test_http_server.py -k "runtime_replay" -v
uv run pytest tests/coding_agent/test_observability.py -v
uv run pytest tests/agentkit/storage/test_pg.py -k "tape" -v
```

## Boundaries

- The smoke tests use in-memory fakes around the existing public/runtime
  contracts and do not add new runtime behavior.
- JSONL/file storage remains the default outside explicit PG/runtime-store
  configuration.
- Replay smoke coverage uses the HTTP API surface and a configured runtime
  store, while tape debug smoke coverage stays at the `PGTapeStore` boundary.
- Trace smoke coverage verifies safe correlation identifiers only. It must not
  export raw prompt, message, result, secret, or text values.
