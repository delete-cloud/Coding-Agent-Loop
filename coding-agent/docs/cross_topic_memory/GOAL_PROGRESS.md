# Cross-topic Memory / Topic Range Search Goal Progress

This ledger tracks G136-G144 for the Cross-topic Memory / Topic Range Search
phase.

## G136_CROSS_TOPIC_MEMORY_CURRENT_STATE_MAP

### Before

- Goal id: G136_CROSS_TOPIC_MEMORY_CURRENT_STATE_MAP
- Intended files:
  - `docs/cross_topic_memory/GOAL_PROGRESS.md`
  - `docs/cross_topic_memory/CURRENT_STATE.md`
- Verification commands:
  - `rg -n "TopicRange|TopicSummary|TopicRangeIndex|memory candidate|ContextPack|Developer Console|observability|Out of scope" docs/cross_topic_memory/CURRENT_STATE.md`
  - `uv run ruff format --check --preview docs/cross_topic_memory/GOAL_PROGRESS.md docs/cross_topic_memory/CURRENT_STATE.md`
  - `git diff --check -- .`
- Stop criteria:
  - Current topic lifecycle, topic recall, Bee artifacts, memory evidence,
    context pack, retrieval, console, and observability surfaces are mapped.
  - Later code-change files and tests to preserve are identified.
  - No production code is changed.

### After

- Changed files:
  - `docs/cross_topic_memory/GOAL_PROGRESS.md`
  - `docs/cross_topic_memory/CURRENT_STATE.md`
- Tests run:
  - `rg -n "TopicRange|TopicSummary|TopicRangeIndex|memory candidate|ContextPack|Developer Console|observability|Out of scope" docs/cross_topic_memory/CURRENT_STATE.md`
  - `uv run ruff format --check --preview docs/cross_topic_memory/GOAL_PROGRESS.md docs/cross_topic_memory/CURRENT_STATE.md`
  - `git diff --check -- .`
- Results:
  - Documented current Topic/Tape lifecycle, recall, provenance, Bee
    report/evidence/memory candidate artifacts, memory plugin behavior,
    ContextPack rendering, retrieval/evaluation, console routes, and
    observability boundaries.
  - Identified exact product-layer files likely to change in G138-G144.
  - Confirmed G136 made no production code changes.
- Remaining risks:
  - G137 still needs the ADR to lock the durable model, review/promotion, recall
    injection, console, and metrics boundaries before production code changes.

## G137_CROSS_TOPIC_MEMORY_ADR

### Before

- Goal id: G137_CROSS_TOPIC_MEMORY_ADR
- Intended files:
  - `docs/cross_topic_memory/GOAL_PROGRESS.md`
  - `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
- Verification commands:
  - `rg -n "TopicRange|TopicSummary|TopicRangeIndex|TopicRecallQuery|TopicRecallResult|TopicRecallAnchor|TopicDerivedMemory|MemoryCandidateReview|AcceptedMemory|RecallContextPack|nmem|Acceptance Criteria" docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
  - `uv run ruff format --check --preview docs/cross_topic_memory/GOAL_PROGRESS.md docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
  - `git diff --check -- .`
- Stop criteria:
  - ADR defines cross-topic memory, topic range search, memory review, accepted
    memory, recall context, no-leak, metrics, and console boundaries.
  - ADR states memory and recall are reference evidence, not system
    instructions.
  - ADR explicitly defers nmem, homelab, production Argo/K8s, desktop, bridge,
    and multi-agent work.
  - No production code is changed.

### After

- Changed files:
  - `docs/cross_topic_memory/GOAL_PROGRESS.md`
  - `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
- Tests run:
  - `rg -n "TopicRange|TopicSummary|TopicRangeIndex|TopicRecallQuery|TopicRecallResult|TopicRecallAnchor|TopicDerivedMemory|MemoryCandidateReview|AcceptedMemory|RecallContextPack|nmem|Acceptance Criteria" docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
  - `uv run ruff format --check --preview docs/cross_topic_memory/GOAL_PROGRESS.md docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
  - `git diff --check -- .`
- Results:
  - Added ADR-0046 defining cross-topic memory and topic range search
    boundaries.
  - Locked memory candidate review, accepted memory, topic recall, context-pack,
    console, observability, and no-leak semantics before production code
    changes.
  - Confirmed G137 made no production code changes.
- Remaining risks:
  - G138 still needs the first production implementation: deterministic
    topic-range indexing/search over finalized topics and sanitized Bee
    summaries.
