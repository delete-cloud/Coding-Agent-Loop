# G35 - Approval Routing

Add approval/HITL routing for high-risk file and command actions.

## Scope

- Keep action approval routing in `coding_agent.action_safety`.
- Route command actions from the G30 command policy verdict.
- Route high-risk patch/file actions from the G27 patch risk and G28 safe edit decision.
- Keep denied actions distinct from approval-required actions.
- Do not wire into live approval coordinator in this goal; G36 owns end-to-end smoke composition.

## Intended Files

- `src/coding_agent/action_safety/approval_routing.py`
- `src/coding_agent/action_safety/__init__.py`
- `tests/coding_agent/action_safety/test_approval_routing.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`

## Postmortem Routing

G35 does not modify approval coordinator/store files. Because it defines approval routing semantics, run focused approval policy tests and review PM-0011 before release.

## Target Tests

- `uv run pytest tests/coding_agent/action_safety/test_approval_routing.py tests/approval/test_policy.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_command_policy.py tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

## Stop Criteria

- Routing requires modifying approval coordinator/store lifecycle semantics.
- Routing would conflate denied actions with approval-required actions.
- Safe summaries require raw commands, raw paths, file content, env values, prompts, messages, results, or secrets.
- More than two fix iterations fail for the same reason.
