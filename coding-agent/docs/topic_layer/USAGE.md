# Topic Layer Usage

The Topic layer is a Coding Agent product/runtime/context abstraction over
existing Tape, Run, ContextPack, memory, evaluation, observability, and console
surfaces.

## Model

- A Topic is a business context unit, not an AgentKit Core primitive.
- A Topic is represented as a tape range bounded by product anchors:
  - `topic_initial`
  - `topic_finalized`
  - `topic_aborted` for aborted ranges
- Topic recall is represented by a `recall_anchor` encoded through existing
  generic tape anchors.
- Topic IDs may appear in durable records, console routes, and trace
  correlation attributes, but not in Prometheus labels.

## Developer Console

Open:

```bash
uv run python -m coding_agent serve --host 127.0.0.1 --port 8080
```

Then inspect:

- `/console/topics` for topic list summaries.
- `/console/topics/{topic_id}` for topic range, anchors, recalls, costs, and
  related runs/actions/validations.
- `/console/observability?run_id={run_id}` for safe trace correlation including
  topic correlation when available.

When PG-backed HTTP storage is configured, the console prefers `PGTopicStore`
for topics, anchors, recall links, and cost aggregates. Without PG-backed topic
storage, it falls back to sanitized run metadata.

## Verification

Focused smoke:

```bash
uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v
```

Representative regression checks:

```bash
uv run pytest tests/coding_agent/test_topic_store.py tests/coding_agent/test_topic_lifecycle.py tests/coding_agent/test_topic_recall.py tests/coding_agent/test_topic_provenance.py -v
uv run pytest tests/ui/test_developer_console.py -k "topic or e2e" -v
uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v
```

## Non-Goals

This phase does not implement schedules, Bee workflow runtime, desktop app,
bridge, proactive agent, multi-agent task graph, or an external executor.
