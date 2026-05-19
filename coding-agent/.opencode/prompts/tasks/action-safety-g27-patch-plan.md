Goal:
Add the G27 patch planning data model, diff summary, and bounded risk classification.

Scope:
- Add a Coding Agent app-level patch plan model.
- Summarize unified diff hunks with counts and positions, not raw patch content.
- Classify patch risk with bounded enum values and reason codes.
- Add deterministic focused tests for safe summaries and risk classification.
- Update the phase ledger before and after implementation.

Out of scope:
- Applying patches through the new plan model.
- Safe edit policy for symlink, binary, size, or workspace validation.
- Dry-run/preview integration in `file_patch`.
- Command policy, validation runner, observability, approval routing, or snapshot/restore.
- AgentKit pipeline changes.

Context:
- ADR:
  - `docs/adr/0035-action-safety-and-workspace-execution.md`
- Current-state docs:
  - `docs/action_safety/CURRENT_STATE.md`
  - `docs/action_safety/GOAL_PROGRESS.md`
- Relevant files:
  - `src/coding_agent/action_safety/patch_plan.py`
  - `src/coding_agent/action_safety/__init__.py`
  - `tests/coding_agent/action_safety/test_patch_plan.py`
  - `src/coding_agent/tools/file_patch_tool.py`

Postmortem routing:
- G27 adds new action-safety files and does not modify existing postmortem `related_files`.
- Later goals that integrate plans into `src/coding_agent/tools/file_patch_tool.py`, file tools, shell tools, or `CoreToolsPlugin` must apply the PM-0001/PM-0009 focused checks.

Target tests:
- `uv run pytest tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Loop policy:
- Engineer implements the smallest correct code change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
- Stop if implementation requires safe edit policy or dry-run behavior from later goals.
- Stop if implementation requires AgentKit pipeline changes.
