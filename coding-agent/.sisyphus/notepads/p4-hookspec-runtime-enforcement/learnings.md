# P4 Learnings

## [2026-03-31] Session Start

### Baseline
- 866 tests passing
- All P3 commits merged (1a5c4dd is HEAD)
- Plan file: docs/superpowers/plans/2026-03-31-p4-hookspec-runtime-enforcement.md

### Key Conventions
- Run tests with: `uv run pytest tests/ -q --tb=short`
- Mypy check: `uv run mypy src/agentkit/runtime/hook_runtime.py src/agentkit/runtime/hookspecs.py src/agentkit/plugin/registry.py src/agentkit/errors.py src/agentkit/runtime/pipeline.py`
- Each task = ONE atomic commit, all tests green before commit
- Evidence saved to: .sisyphus/evidence/p4-task-{N}-{scenario-slug}.txt

### Critical Constraints
- Do NOT break HookRuntime(registry) with no specs
- Do NOT introduce ctx._handoff_done (P3 already added it)
- Do NOT modify ApprovalPlugin or AskUser handler logic
- Do NOT validate observer hooks return types (on_error, on_checkpoint)
- agentkit must NOT import coding_agent
- approve_tool_call Pipeline guard MUST fail closed (reject), not auto-approve

### Architecture
- HookError base class has `hook_name` attribute — HookTypeError extends it
- HOOK_SPECS currently has 13 hooks — T2 adds execute_tools_batch to make 14
- Pipeline._stage_build_context lines 145-188 has P3 logic (window_start, _handoff_done) — T7 must PRESERVE this

### Task Dependencies
- T1 (quick) → T2 (deep) → T3 (deep) → T4 (quick) and T7 (deep) in parallel → T5 (quick, independent until T6) → T6 (quick, after T4+T5) → T8 (quick, after T6+T7)
- T1 and T5 can actually start independently (T5 has no dependencies)
