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
- P2 NOT STARTED

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
