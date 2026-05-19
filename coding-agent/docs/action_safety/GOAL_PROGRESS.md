# Action Safety And Workspace Execution Goal Progress

Date started: 2026-05-19
Baseline: Context System G12-G24 is complete on `main`.

This file is the phase ledger for G25-G37. Before each goal, append the goal id, intended files, verification commands, and stop criteria. After each goal, append changed files, tests run, results, and remaining risks.

## Phase Constraints

- Keep AgentKit Core generic.
- Do not rewrite the AgentKit pipeline.
- Do not modify durable runtime G00-G11 semantics.
- Do not modify Context System G12-G24 semantics unless required for validation feedback integration.
- Do not remove JSONL compatibility.
- Do not require real external LLM calls, production credentials, or external services for tests.
- Do not implement schedules, desktop, bridge, proactive autonomous-agent behavior, or full Docker sandboxing in this phase.
- Do not run or fix full-repository ruff unless a goal explicitly scopes it.
- Do not add raw prompt, content, message, result, secret, or text values to trace attributes.
- Prefer deterministic tests with fixtures, fakes, and temporary workspaces.

## Goal Map

No pre-existing repository document defined G25-G37 individually. The following map decomposes the requested phase into sequential, reviewable goals.

| Goal | Scope |
| --- | --- |
| G25 | Current-state audit, phase goal map, and task-packet/ledger setup. |
| G26 | ADR for action-safety and workspace-execution boundaries. |
| G27 | Patch planning data model, diff summary, and risk classification. |
| G28 | Safe file edit policy for path, size, symlink, binary, and workspace-boundary validation. |
| G29 | Patch application flow with dry-run/preview validation and deterministic failure reporting. |
| G30 | Command execution policy model for allow, deny, approval, cwd/env, timeout, and path escape risk. |
| G31 | Validation/test runner contract with deterministic command specs and structured outcomes. |
| G32 | Action observability spans/events with safe metadata only. |
| G33 | Workspace snapshot/restore MVP for local temporary workspaces. |
| G34 | Validation feedback integration after edits and commands without changing context-system semantics. |
| G35 | Approval/HITL routing for high-risk file and command actions. |
| G36 | End-to-end safe action smoke test across patch, command, validation, and restore behavior. |
| G37 | Final implementation report, acceptance audit, durable/context baseline verification, and cleanup. |

## G25 - Current-State Audit And Phase Ledger

Status: passed local verification; pending PR.

### Before

Goal id: G25

Intended files:

- `docs/action_safety/GOAL_PROGRESS.md`
- `docs/action_safety/CURRENT_STATE.md`
- `.opencode/prompts/tasks/action-safety-g25-current-state.md`

Verification commands:

- `test -f docs/action_safety/GOAL_PROGRESS.md`
- `test -f docs/action_safety/CURRENT_STATE.md`
- `test -f .opencode/prompts/tasks/action-safety-g25-current-state.md`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_workspace_archive.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Stop criteria:

- Cannot identify current file, patch, shell, approval, and workspace archive entrypoints.
- Documentation requires changing production runtime semantics.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- The current-state audit consulted `postmortem/index.yaml`.
- Action-safety source surfaces match PM-0001 and PM-0009 release checks through `src/coding_agent/tools/file_ops.py`, `src/coding_agent/tools/shell.py`, and `src/coding_agent/plugins/core_tools.py`.
- G25 is docs-only; later goals touching those files must include focused tests for the affected tools and review the changed control-flow shape before release.

### After

Changed files:

- `docs/action_safety/GOAL_PROGRESS.md`
- `docs/action_safety/CURRENT_STATE.md`
- `.opencode/prompts/tasks/action-safety-g25-current-state.md`

Tests run:

- `test -f docs/action_safety/GOAL_PROGRESS.md`
- `test -f docs/action_safety/CURRENT_STATE.md`
- `test -f .opencode/prompts/tasks/action-safety-g25-current-state.md`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_workspace_archive.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Results:

- File existence checks passed.
- File tools, shell tool, approval policy, and workspace archive tests passed: 39 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.

Remaining risks:

- G25 is docs-only and does not prove the future action-safety implementation.
- The G25-G37 goal map is inferred from the phase objective because no prior repository document defined those individual goal ids.
- Command policy, patch planning, validation runner, observability, approval risk routing, local/cloud parity, and snapshot/restore behavior remain future goals.
