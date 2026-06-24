Goal:
Implement ADR-0072 Decision B semantic tape-memory index v0 as the smallest
safe slice: generic AgentKit index protocol/model plus Coding Agent policy for
safe semantic memory indexing/querying and deterministic hybrid merge.

Scope:
- Add generic `MemoryIndex` / `MemoryHit` protocol/model in AgentKit storage
  protocols without importing Coding Agent topic types.
- Add Coding Agent semantic memory index helpers that validate recall-safe text
  before indexing and before query embedding/search.
- Add deterministic hybrid merge for deterministic topic recall and semantic
  memory hits: stable identity de-duplication, deterministic ordering, and
  deterministic path tie-breaks.
- Add focused tests for ADR-0072 Decision B acceptance criteria.

Out of scope:
- Real LanceDB/OpenAI embedding wiring for tape memory.
- New product config for enabling semantic retrieval.
- Replacing `TopicRangeIndex` or changing default deterministic recall.
- Moving topic/review/provenance/product models into AgentKit.

Context:
- ADRs:
  - docs/adr/0072-tape-memory-semantic-index-and-enable-switch.md
  - docs/adr/0046-cross-topic-memory-and-topic-range-search.md
  - docs/adr/0069-local-memory-review-store-persistence.md
- Relevant files:
  - src/agentkit/storage/protocols.py
  - src/coding_agent/topics/range_index.py
  - src/coding_agent/topics/recall_context.py
  - src/coding_agent/topics/memory.py
  - tests/coding_agent/test_recall_context.py
  - tests/coding_agent/test_topic_range_index.py
  - tests/coding_agent/test_semantic_tape_index.py

Target tests:
- uv run pytest tests/coding_agent/test_semantic_tape_index.py -q
- uv run pytest tests/coding_agent/test_recall_context.py tests/coding_agent/test_topic_range_index.py tests/coding_agent/test_memory_review.py -q
- uv run ruff check src/agentkit/storage/protocols.py src/coding_agent/topics/ tests/coding_agent/test_semantic_tape_index.py
- uv run ruff format --check src/agentkit/storage/protocols.py src/coding_agent/topics/ tests/coding_agent/test_semantic_tape_index.py

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
