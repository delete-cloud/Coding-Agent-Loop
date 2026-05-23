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

## G138_TOPIC_RANGE_INDEX_AND_SEARCH

### Before

- Goal id: G138_TOPIC_RANGE_INDEX_AND_SEARCH
- Intended files:
  - `docs/cross_topic_memory/GOAL_PROGRESS.md`
  - `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
  - `src/coding_agent/topic_range_index.py`
  - `tests/coding_agent/test_topic_range_index.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_topic_range_index.py -v`
  - `uv run pytest tests/coding_agent/test_topic_recall.py tests/coding_agent/test_topic_layer_smoke.py -v`
  - `uv run ruff format --check --preview docs/cross_topic_memory/GOAL_PROGRESS.md docs/adr/0046-cross-topic-memory-and-topic-range-search.md src/coding_agent/topic_range_index.py tests/coding_agent/test_topic_range_index.py`
  - `uv run ruff check src/coding_agent/topic_range_index.py tests/coding_agent/test_topic_range_index.py`
  - `git diff --check -- .`
- Stop criteria:
  - Finalized topic summaries can be indexed and searched deterministically.
  - Open topics are skipped by default unless explicitly indexed.
  - Bee task metadata, report refs, evidence refs, and sanitized summaries can
    be attached to topic range search results.
  - Search supports text, kind/profile, Bee template ID, tags, status, and time
    range filters.
  - Raw stdout/stderr/env/command output/secret-like text is rejected.

### After

- Changed files:
  - `docs/cross_topic_memory/GOAL_PROGRESS.md`
  - `docs/adr/0046-cross-topic-memory-and-topic-range-search.md`
  - `src/coding_agent/topic_range_index.py`
  - `tests/coding_agent/test_topic_range_index.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_topic_range_index.py -v`
  - `uv run pytest tests/coding_agent/test_topic_recall.py tests/coding_agent/test_topic_layer_smoke.py -v`
  - `uv run ruff format --check --preview docs/cross_topic_memory/GOAL_PROGRESS.md docs/adr/0046-cross-topic-memory-and-topic-range-search.md src/coding_agent/topic_range_index.py tests/coding_agent/test_topic_range_index.py`
  - `uv run ruff check src/coding_agent/topic_range_index.py tests/coding_agent/test_topic_range_index.py`
  - `git diff --check -- .`
- Results:
  - Added deterministic in-process `TopicRangeIndex` for finalized topic
    summaries and sanitized Bee task/report/evidence metadata.
  - Added search by text, kind, profile, Bee template ID, tags, status, and
    created-at bounds.
  - Added no-leak validation rejecting raw stdout/stderr/env/command output/log
    and secret-like text before indexing or querying.
  - Preserved existing topic recall and topic layer smoke behavior.
- Remaining risks:
  - G139 still needs topic-derived memory candidate generation from finalized
    topics and Bee task outputs.
