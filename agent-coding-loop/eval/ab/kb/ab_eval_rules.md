# A/B Evaluation Rules

Use the same task set and runtime settings for No-RAG and RAG experiments.

Track at least these metrics:

- pass_rate
- avg_duration_sec
- kb_signal_rate (on tasks that require KB)
- citation_recall_avg (on tasks that require KB)
- repo_kb_overuse_rate (on tasks that should not use KB)

Report both metric improvements and latency trade-offs.
