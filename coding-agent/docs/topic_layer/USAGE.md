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

The console prefers the product-selected durable `TopicStore` for topics,
anchors, recall links, and cost aggregates:

- local SQLite durable bundles use `SQLiteTopicStore` on the same
  `local.sqlite3` file as session, tape, checkpoint, runtime, and owner state;
- PostgreSQL durable deployments use `PGTopicStore`;
- custom, mixed, or non-durable storage modes have no selected durable
  `TopicStore` and fall back to sanitized run metadata for read-only
  inspection.

The metadata fallback is not an authoritative semantic rebuild source.
Topic-backed semantic full rebuild requires a selected durable `TopicStore` and
must fail before clearing any derived semantic backend if no complete durable
topic authority is available.

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
