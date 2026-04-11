# 2026-04-02 Tape-View — Decisions

## 2026-04-10 closure re-check
- Final verdict: `minimal-tail-then-closure`.
- Task 7 executed to close the localized incremental append inconsistency in the incremental append path.
- Fresh Task 8 closure verification passed via `uv run pytest tests/agentkit/tape/test_view.py tests/agentkit/context/test_builder.py tests/agentkit/runtime/test_pipeline.py -v`.
- Tape-view closes with the approved tail fix in place: incremental append now routes through `TapeView.from_tape(ctx.tape)` and appends `view.entries[visible_start:]`.
