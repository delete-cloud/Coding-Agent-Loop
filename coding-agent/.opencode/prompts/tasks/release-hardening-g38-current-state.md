# G38 - Release Hardening Current State

Record the current release-hardening baseline and create the phase ledger for G38-G45.

Scope:

- Create `docs/release_hardening/CURRENT_STATE.md`.
- Create or update `docs/release_hardening/GOAL_PROGRESS.md`.
- Define a sequential G38-G45 goal map because no existing repository document defines those goal ids.
- Do not change runtime, context-system, action-safety, persistence, CLI, or package behavior in G38.

Verification:

- `test -f docs/release_hardening/CURRENT_STATE.md`
- `test -f docs/release_hardening/GOAL_PROGRESS.md`
- `test -f .opencode/prompts/tasks/release-hardening-g38-current-state.md`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `git diff --check -- .`
