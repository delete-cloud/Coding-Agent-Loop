## [2026-03-30] Init — P1 topic-skeleton

### Architecture Invariants
- agentkit imports NOTHING from coding_agent (strict single-direction)
- Entry is frozen=True — immutable after creation
- Tape is append-only (append() + handoff() only, never splice)
- Plugin protocol: must have `state_key: str` and `hooks() -> dict`

### Test Patterns
- Tests live in `tests/agentkit/` or `tests/coding_agent/plugins/`
- Test files for agentkit context: `tests/agentkit/context/` (may need __init__.py)
- HookSpecs test hardcodes exact set of hook names — update the test FIRST (TDD)
- All test classes use plain pytest (no asyncio for unit tests on sync plugins)
- Pipeline tests use @pytest.mark.asyncio for async stage tests

### Key File Locations
- hookspecs: src/agentkit/runtime/hookspecs.py
- pipeline: src/agentkit/runtime/pipeline.py
- builder: src/agentkit/context/builder.py
- test_hookspecs: tests/agentkit/runtime/test_hookspecs.py
- test_pipeline: tests/agentkit/runtime/test_pipeline.py (already has asyncio tests)

### Hook Runtime Dispatch
- `notify()` = fire-and-forget observer (swallows exceptions)
- `call_first()` = returns first non-None result
- `call_many()` = collects all non-None results

### Current State
- 807 tests passing, 4 pre-existing failures (test_kb x3, test_security x1)
- P0 changes uncommitted in working tree

## [2026-03-30] TopicPlugin skeleton complete

### TopicPlugin Implementation Notes
- `state_key = "topic"` (class attribute, not instance)
- `hooks()` returns `on_checkpoint`, `on_session_event`, and `mount` (as `do_mount` callable)
- `do_mount()` returns `{"current_topic_id": None, "topic_count": 0}` on init
- `on_session_event` is a no-op pass-through (future use)
- `on_checkpoint(ctx=ctx)` reads `ctx.tape` directly — no staging through plugin_states for detection
- Topic switch triggers: `_end_topic` (appends `topic_finalized` anchor) → `_start_topic` (appends `topic_initial` anchor)
- Entry order in tape: `topic_initial` → ... turn entries ... → `topic_finalized` → `topic_initial` → ...

### File Path Extraction
- Scans `tool_call` entries with `arguments` dict keys: `path`, `file`, `filename`, `file_path`
- Also scans `message` content with regex `[\w./]+\.\w{1,10}` (capped at 5 per message)
- Only considers entries AFTER the last `user` message in the tape (recent turn)

### Overlap Detection Boundary
- Overlap ratio = `len(recent_files & prev_topic_files) / max(len(prev_topic_files), 1)`
- If overlap_ratio < overlap_threshold → switch topic
- If recent_files is empty → no switch (pure conversation turns)
- If `_current_topic_files` is empty but topic exists → absorb files, no switch

### Test Pattern Confirmed
- `FakeCtx` with `tape` and `plugin_states = {}` is sufficient for unit tests
- Multi-turn tests: add entries to tape then call `on_checkpoint` for each turn boundary
- `tape.filter("anchor")` correctly returns all anchors regardless of window
