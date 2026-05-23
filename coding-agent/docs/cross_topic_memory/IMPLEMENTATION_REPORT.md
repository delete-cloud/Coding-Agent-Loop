# Cross-topic Memory Implementation Report

## Scope

G136-G144 added cross-topic recall, topic range search, topic-derived memory
candidates, review/promotion, recall-aware context packaging, recall metrics,
and Developer Console memory/recall visibility.

This phase stayed in `src/coding_agent/`. AgentKit Core was not changed.

## Landed Goals

- G136: mapped current Topic, Bee, memory, context, console, and observability
  state in `CURRENT_STATE.md`.
- G137: accepted ADR-0046 for cross-topic memory and topic range search
  boundaries.
- G138: implemented deterministic `TopicRangeIndex` and search.
- G139: added topic-derived and Bee-derived memory candidate helpers.
- G140: added local deterministic memory review and accepted-memory rendering.
- G141: added recall planner, recall anchors/links, and recall context pack.
- G142: added recall evaluation variants and low-cardinality recall/memory
  metrics.
- G143: added read-only Developer Console memory review/provenance rendering.
- G144: added end-to-end smoke coverage and usage/report documentation.

## Verification

Primary smoke:

```bash
uv run pytest tests/coding_agent/test_cross_topic_memory_smoke.py -v
```

Representative regression checks:

```bash
uv run pytest tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_topic_memory.py tests/coding_agent/test_memory_review.py tests/coding_agent/test_recall_context.py tests/coding_agent/test_recall_evaluation.py -v
uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v
uv run pytest tests/coding_agent/test_context_system_smoke.py -v
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_observability.py -v
uv run pytest tests/coding_agent/test_observability_platform_smoke.py -v
uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v
uv run pytest tests/integration/test_durable_runtime_smoke.py -v
git diff --check -- .
```

## Safety Notes

- Recall and accepted memory are reference evidence only.
- Candidate memory requires explicit review before accepted-memory reuse.
- Prometheus metrics use low-cardinality labels such as source, status, kind,
  and review status. IDs are not labels.
- OTLP and console rendering filter raw or sensitive strings before display or
  export.

## Remaining Work

External memory backends such as nmem, homelab-specific memories, production
Kubernetes/Argo integrations, desktop/bridge work, and multi-agent task graphs
remain out of scope for this phase.
