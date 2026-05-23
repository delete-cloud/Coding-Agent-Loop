# Cross-topic Memory Usage

Cross-topic memory lets Coding Agent reuse prior finalized topic and Bee task
experience as reference context.

## Flow

1. Finalize a topic with a safe summary.
2. Index the finalized topic with `TopicRangeIndex.index_topic()`.
3. Generate candidate memories with
   `propose_memory_candidate_from_topic()` or
   `propose_memory_candidates_from_bee_artifacts()`.
4. Review candidates with `MemoryReviewStore`.
5. Build recall with `TopicRecallPlanner`.
6. Render recall evidence with `recall_context_pack()`.
7. Inspect memory candidates and recall links in the Developer Console.

## Guarantees

- Memory is reference-only context, not system instruction.
- Candidate memory is not accepted until explicitly reviewed.
- Memory candidate provenance preserves topic/task/run/evidence references.
- Recall links preserve source topic, recalled topic, relation, and anchor
  provenance.
- Metrics use low-cardinality labels only.
- Raw prompts, messages, command output, stdout/stderr, env, raw logs, and
  secrets are rejected or omitted from recall, memory, metrics, traces, and
  console views.

## Local Smoke

```bash
uv run pytest tests/coding_agent/test_cross_topic_memory_smoke.py -v
uv run pytest tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_topic_memory.py tests/coding_agent/test_memory_review.py tests/coding_agent/test_recall_context.py tests/coding_agent/test_recall_evaluation.py -v
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_observability.py -v
```
