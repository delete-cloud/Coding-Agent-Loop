# P2 Learnings

## 2026-03-30 — Project Setup

### Key conventions
- All implementation is in `src/coding_agent/plugins/`
- Tests are in `tests/coding_agent/plugins/`
- Framework layer is `src/agentkit/` — do NOT import from coding_agent in agentkit
- Provider: `copilot` / model: `gpt-4.1` (never openai directly)
- Run tests with: `uv run pytest tests/ -x -q`
- Type check: `uv run mypy src/... --ignore-missing-imports`

### Current state
- 826 tests passing (P0 + P1 committed)
- Historical note: this file originally marked P2 as `NOT STARTED`, but closure verification on 2026-04-10 showed the implementation was already present and green.

### Plugin hook patterns
- `hooks()` returns dict mapping hook name → method
- `on_checkpoint(ctx=ctx, runtime=runtime)` — ctx has .tape and .plugin_states
- `on_session_event(event_type=str, payload=dict)` — emitted by TopicPlugin via runtime.notify()
- `build_context(tape=tape)` — returns list[dict] grounding messages
- `resolve_context_window(tape=tape)` — returns (split_point, anchor_Entry) | None

### Entry.meta usage
- `entry.meta` is a `dict[str, Any]` — added in P0
- `topic_initial` anchor: `meta={"anchor_type": "topic_initial", "topic_id": str, "topic_number": int}`
- `topic_finalized` anchor: `meta={"anchor_type": "topic_finalized", "topic_id": str, "files": list[str]}`
- `handoff` anchor: `meta={"anchor_type": "handoff", ...}`

### Tape API
- `tape.windowed_entries()` — returns entries from window_start onward
- `tape.append(entry)` — append-only
- Windowing bug known but NOT blocking Tasks 1, 2, 3 (only affects runtime pipeline execution, not plugin unit tests)

### Task independence
- Task 1 (Memory), Task 2 (Metrics), Task 3 (Summarizer) are independent — can run in parallel
- Task 4 (verification) runs after all three complete

## 2026-04-10 — Closure verification

### Fresh verification
- `uv run pytest tests/coding_agent/plugins/test_memory.py tests/coding_agent/plugins/test_metrics.py tests/coding_agent/plugins/test_summarizer.py tests/coding_agent/plugins/test_topic.py -q` → `83 passed`
- `uv run pytest tests/coding_agent/plugins/ -v` → `264 passed, 3 warnings`
- `uv run pytest tests/ -v` → `1623 passed, 31 warnings`
- `uv run mypy src/coding_agent/plugins/memory.py src/coding_agent/plugins/metrics.py src/coding_agent/plugins/summarizer.py src/coding_agent/plugins/topic.py` → success, 0 issues

### Grounded findings
- MemoryPlugin already has topic-scoped recall (`_topic_file_tags`, `on_checkpoint`, filtered `build_context`).
- SessionMetricsPlugin already archives per-topic metrics via `on_session_event(topic_start/topic_end)`.
- SummarizerPlugin already folds at topic boundaries via `resolve_context_window` and fold-boundary anchors.
- TopicPlugin already emits `topic_start` / `topic_end` session events.

### Closure note
- The main blocker to closing P2 was stale tracking, not missing implementation.
- The plan's smoke example still mentions `split point at index 12`, but the current tests and live smoke agree on `split_point == 8` for the covered scenario.
- Closure evidence saved to `.sisyphus/evidence/p2-closure-2026-04-10.txt`.
