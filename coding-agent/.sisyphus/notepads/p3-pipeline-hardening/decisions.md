# P3 Pipeline Hardening — Decisions

## Architecture decisions
- Phase 4 (structlog tracing): INCLUDED per user request ("task 4 也一起开始干吧")
- Phase 4 is still marked optional in plan text but WILL be executed this session
- handler in Task 7/8: prefer public method `plugin.add_memory(record)` over `_memories` direct access
- Directive return flow CONFIRMED: pipeline.py:335 → call_many("on_turn_end") collects non-None values → pipeline.py:338-341 executes via DirectiveExecutor. No pipeline changes needed for Phase 3.

