# Topic Layer Implementation Report

## Scope

G77-G84 added a Topic layer over existing Coding Agent tape, runtime, context,
memory, evaluation, observability, and console capabilities. AgentKit Core
remains generic; Topic is a Coding Agent abstraction.

## Landed Goals

| Goal | Summary | PR |
| --- | --- | --- |
| G77 | Current-state map for tape, run, context, memory, eval, console, observability, and workspace readiness. | #285 |
| G78 | Topic Layer / Tape View ADR. | #286 |
| G79 | Durable topic schema and `PGTopicStore`. | #287 |
| G80 | Topic lifecycle anchors and tape range helpers. | #288 |
| G81 | Deterministic topic recall and context-pack helpers. | #289 |
| G82 | Topic cost, eval, memory provenance helpers and safe topic metric labels. | #290 |
| G83 | Developer Console topic list/detail and safe observability integration. | #291 |
| G84 | Final smoke tests and usage/report docs. | #292 |

## Acceptance Audit

- Topic is modeled as a tape range bounded by `topic_initial` and
  `topic_finalized` product anchors.
- Aborted topics are represented with explicit aborted status and
  `topic_aborted` product anchor metadata.
- Recall writes a `recall_anchor` and persists a topic recall link.
- ContextPack recall evidence includes source topic IDs and source entry ranges.
- Eval and memory provenance can reference topic IDs and entry ranges.
- Topic cost aggregates include token, run, action, validation, and tool-call
  counts.
- Developer Console exposes `/console/topics` and `/console/topics/{topic_id}`.
- Console prefers durable `PGTopicStore` data when PG-backed HTTP storage is
  configured and falls back to sanitized run metadata otherwise.
- Prometheus labels remain low-cardinality. `topic_id` is forbidden as a
  Prometheus label and route template labels avoid `topic_id`.

## Verification Evidence

Fresh G84 focused verification:

```bash
uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v
```

Phase regression verification:

```bash
uv run pytest tests/coding_agent/test_topic_store.py tests/coding_agent/test_topic_lifecycle.py tests/coding_agent/test_topic_recall.py tests/coding_agent/test_topic_provenance.py -v
uv run pytest tests/ui/test_developer_console.py -k "topic or e2e" -v
uv run pytest tests/coding_agent/test_context_system_smoke.py -v
uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v
uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v
uv run pytest tests/integration/test_durable_runtime_smoke.py -v
git diff --check -- .
```

## Remaining Risks

- Topic runtime wiring remains explicit/helper-based. This phase does not start
  schedules, Bee workflows, proactive agents, or multi-agent DAG execution.
- Full production PG verification depends on a configured local PG environment;
  deterministic tests use fake stores and local fixtures.
