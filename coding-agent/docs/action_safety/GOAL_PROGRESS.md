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

Status: merged via PR #227.

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

## G26 - Action-Safety Boundary ADR

Status: merged via PR #228.

### Before

Goal id: G26

Intended files:

- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g26-adr.md`

Verification commands:

- `test -f docs/adr/0035-action-safety-and-workspace-execution.md`
- `test -f .opencode/prompts/tasks/action-safety-g26-adr.md`
- `rg -n "## Context|## Decision|## Alternatives Rejected|## Acceptance Criteria|## References" docs/adr/0035-action-safety-and-workspace-execution.md`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_workspace_archive.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Stop criteria:

- ADR would require rewriting the AgentKit pipeline.
- ADR would require durable runtime semantic changes.
- ADR would require changing context-system semantics in G26.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G26 files are docs/task-packet files and do not directly match `postmortem/index.yaml` `related_files`.
- The ADR records PM-0001/PM-0009 implications for later production changes touching file, shell, or core tool surfaces.

### After

Changed files:

- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g26-adr.md`

Tests run:

- `test -f docs/adr/0035-action-safety-and-workspace-execution.md`
- `test -f .opencode/prompts/tasks/action-safety-g26-adr.md`
- `rg -n "## Context|## Decision|## Alternatives Rejected|## Acceptance Criteria|## References" docs/adr/0035-action-safety-and-workspace-execution.md`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_workspace_archive.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Results:

- ADR and G26 task packet existence checks passed.
- ADR required-section check passed.
- File tools, shell tool, approval policy, workspace archive, cloud environment, and local environment tests passed: 51 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.

Remaining risks:

- G26 is docs-only and does not prove the future action-safety implementation.
- ADR-0035 acceptance criteria intentionally describe future G27-G37 implementation tests that do not exist yet.
- ADR-0035 remains `Proposed` until implementation goals prove and check off the criteria.

## G27 - Patch Planning Data Model

Status: merged via PR #229.

### Before

Goal id: G27

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/patch_plan.py`
- `tests/coding_agent/action_safety/test_patch_plan.py`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g27-patch-plan.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Patch planning requires safe edit policy, workspace validation, or dry-run behavior from later goals.
- Implementation would expose raw patch/file content in plan summaries.
- Implementation would require AgentKit pipeline changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G27 production/test files are new and do not directly match `postmortem/index.yaml` `related_files`.
- Later integration into existing file, shell, or core tool surfaces must apply PM-0001/PM-0009 focused checks.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/patch_plan.py`
- `tests/coding_agent/action_safety/test_patch_plan.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g27-patch-plan.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Patch planning tests passed: 6 passed.
- File tools, shell tool, approval policy, cloud environment, and local environment tests passed: 34 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Remaining risks:

- G27 does not integrate patch plans into `file_patch`; G29 owns dry-run/preview and apply flow.
- G27 intentionally does not perform safe edit validation for symlinks, binary files, size, or workspace paths; G28 owns that policy.
- Risk classification is bounded and deterministic but intentionally coarse until later goals add action policy and approval routing.
- Local review found and G27 fixed two parser correctness risks: multi-file diffs are rejected for single-path plans, and hunk body counts must match hunk headers.

## G28 - Safe File Edit Policy

Status: merged via PR #230.

### Before

Goal id: G28

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/safe_edit.py`
- `tests/coding_agent/action_safety/test_safe_edit.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g28-safe-edit.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Safe edit policy requires patch apply/dry-run integration from G29.
- Implementation would expose raw file content in decision summaries.
- Implementation would require AgentKit pipeline changes.
- Deterministic verification cannot be produced.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G28 production/test files are new or action-safety package exports and do not directly match `postmortem/index.yaml` `related_files`.
- Later integration into existing file, patch, shell, or core tool surfaces must apply PM-0001/PM-0009 focused checks.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/safe_edit.py`
- `tests/coding_agent/action_safety/test_safe_edit.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g28-safe-edit.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Safe edit and patch planning tests passed: 12 passed.
- File tools, shell tool, approval policy, cloud environment, and local environment tests passed: 34 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Remaining risks:

- G28 does not integrate the safe edit policy into `file_write`, `file_replace`, or `file_patch`; G29 owns patch application integration.
- G28 is local filesystem policy only. Cloud execution still depends on provider/client enforcement until later local/cloud action policy integration.
- Missing nested parent creation is denied for now; future tool integration can decide whether to create parents after explicit policy coverage.
- Local review found and G28 fixed a create-target parent validation risk: create under an existing regular file is rejected.

## G29 - Patch Dry-Run And Preview Validation

Status: merged via PR #231.

### Before

Goal id: G29

Intended files:

- `src/coding_agent/tools/file_patch_tool.py`
- `tests/coding_agent/tools/test_file_patch_tool.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g29-patch-apply.md`

Verification commands:

- `uv run pytest tests/coding_agent/tools/test_file_patch_tool.py tests/coding_agent/action_safety/test_patch_plan.py tests/coding_agent/action_safety/test_safe_edit.py -v`
- `uv run pytest tests/coding_agent/environment/test_local_environment.py tests/agentkit/environment/test_protocols.py tests/coding_agent/plugins/test_core_tools_parity.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety src/coding_agent/tools/file_patch_tool.py tests/coding_agent/action_safety tests/coding_agent/tools/test_file_patch_tool.py`
- `uv run ruff check src/coding_agent/action_safety src/coding_agent/tools/file_patch_tool.py tests/coding_agent/action_safety tests/coding_agent/tools/test_file_patch_tool.py`
- `git diff --check -- .`

Stop criteria:

- Patch apply integration requires multi-file transactions.
- Implementation would expose raw patch/file content in preview summaries.
- Implementation would require command policy or approval routing from later goals.
- Implementation would require AgentKit pipeline changes.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- `src/coding_agent/tools/file_patch_tool.py` is not listed directly in `postmortem/index.yaml`.
- Because patch execution is registered through `CoreToolsPlugin`, G29 applies PM-0001/PM-0009 style focused tool-control-flow checks and local environment parity tests.

### After

Changed files:

- `src/coding_agent/tools/file_patch_tool.py`
- `tests/coding_agent/tools/test_file_patch_tool.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g29-patch-apply.md`

Tests run:

- `uv run pytest tests/coding_agent/tools/test_file_patch_tool.py tests/coding_agent/action_safety/test_patch_plan.py tests/coding_agent/action_safety/test_safe_edit.py -v`
- `uv run pytest tests/coding_agent/environment/test_local_environment.py tests/agentkit/environment/test_protocols.py tests/coding_agent/plugins/test_core_tools_parity.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety src/coding_agent/tools/file_patch_tool.py tests/coding_agent/action_safety tests/coding_agent/tools/test_file_patch_tool.py`
- `uv run ruff check src/coding_agent/action_safety src/coding_agent/tools/file_patch_tool.py tests/coding_agent/action_safety tests/coding_agent/tools/test_file_patch_tool.py`
- `git diff --check -- .`

Results:

- Patch dry-run, patch planning, and safe edit tests passed: 18 passed.
- Local environment, AgentKit environment protocol, and core tool parity tests passed: 16 passed.
- File tools, shell tool, approval policy, and cloud environment tests passed: 31 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for changed action-safety and patch-tool files.
- Diff whitespace check passed.

Remaining risks:

- G29 integrates local `file_patch` only. Cloud `file_patch` still delegates to the cloud client.
- G29 keeps multi-file transactions out of scope.
- Approval routing and command policy remain future goals.
- Local review found and G29 fixed a compatibility regression: standard single-file git diffs are accepted in both dry-run and default apply paths.

## G30 - Command Execution Policy Model

Status: merged via PR #232.

### Before

Goal id: G30

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/command_policy.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g30-command-policy.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_command_policy.py tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Command policy model requires `bash_run` execution integration.
- Command policy model requires approval coordinator routing.
- Implementation would expose raw command arguments or environment values in safe summaries.
- Implementation would require AgentKit pipeline changes.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G30 production/test files are action-safety files and do not directly match `postmortem/index.yaml` `related_files`.
- Later shell/core tool integration must apply PM-0001/PM-0009 focused checks.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/command_policy.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g30-command-policy.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_command_policy.py tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Command policy, safe edit, and patch planning tests passed: 20 passed.
- Shell tool, approval policy, cloud environment, and local environment tests passed: 28 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Remaining risks:

- G30 does not wire command policy into local or cloud `bash_run`; later goals own execution integration and approval routing.
- Validation command handling is a policy classification only; G31 owns validation runner execution and outcomes.
- The allow/approval command lists are intentionally conservative and can be refined when command policy is integrated into execution.
- Local review found and G30 fixed three policy risks: validation commands cannot bypass destructive/package-install approval, raw shell syntax is denied before local/cloud execution, and safe summaries sanitize command names.

## G31 - Validation Runner Contract

Status: merged via PR #233.

### Before

Goal id: G31

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/command_policy.py`
- `src/coding_agent/action_safety/validation_runner.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `tests/coding_agent/action_safety/test_validation_runner.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g31-validation-runner.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_verification.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Validation runner requires modifying AgentKit pipeline stages.
- Validation runner requires context-system authority or evidence semantics changes.
- Outcomes require raw command output, raw env values, or raw tool arguments in safe summaries.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G31 production/test files are action-safety files and do not directly match `postmortem/index.yaml` `related_files`.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/command_policy.py`
- `src/coding_agent/action_safety/validation_runner.py`
- `tests/coding_agent/action_safety/test_command_policy.py`
- `tests/coding_agent/action_safety/test_validation_runner.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g31-validation-runner.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_verification.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Validation runner and command policy tests passed: 14 passed.
- Existing task-packet verification tests passed: 16 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Local review:

- Local review found and G31 fixed three validation-runner safety risks: absolute executable paths outside the workspace are denied, cloud validation requests fail fast instead of executing locally, and validation subprocesses no longer inherit the full parent process environment.

Remaining risks:

- G31 adds a standalone action-safety validation runner; it does not yet wire validation outcomes into shell tools, approvals, observability, or context evidence.
- Validation outcome summaries intentionally avoid raw stdout/stderr; later user-facing reporting may need a separate local-only channel if raw output is required.

## G32 - Action Observability

Status: merged via PR #234.

### Before

Goal id: G32

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/action_observability.py`
- `tests/coding_agent/action_safety/test_action_observability.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g32-observability.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_action_observability.py tests/coding_agent/test_observability.py tests/agentkit/observability/test_core.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Implementation requires AgentKit observability model changes.
- Safe attributes require raw prompts, content, messages, results, secrets, command output, file content, env values, or free-form text.
- Implementation requires wiring action observability into live tool execution paths before G35/G36.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- Intended G32 production/test files are action-safety files and do not directly match `postmortem/index.yaml` `related_files`.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/action_observability.py`
- `tests/coding_agent/action_safety/test_action_observability.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g32-observability.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_action_observability.py tests/coding_agent/test_observability.py tests/agentkit/observability/test_core.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Action observability, Coding Agent observability, and AgentKit observability tests passed: 19 passed.
- Validation runner and command policy regression tests passed: 14 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Local review:

- Local review found and G32 fixed three observability risks: action event/span names are no longer caller-controlled, event sink failures fail open, and action spans now expose a safe updater for final metadata.

Remaining risks:

- G32 adds metadata-only action observation primitives; it does not yet wire them into live file, command, approval, or restore execution paths.
- String-valued metadata is intentionally restricted to bounded labels; callers that need rich user-facing explanations must keep those outside trace attributes.

## G33 - Workspace Snapshot Restore

Status: merged via PR #235.

### Before

Goal id: G33

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/workspace_snapshot.py`
- `tests/coding_agent/action_safety/test_workspace_snapshot.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g33-snapshot-restore.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_workspace_snapshot.py tests/coding_agent/environment/test_workspace_archive.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_action_observability.py tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Restore requires remote workspace live sync or Docker sandboxing.
- Restore cannot validate the snapshot before clearing the target workspace.
- Restore would overwrite or delete `.git`.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G33 adds action-safety files and does not directly match `postmortem/index.yaml` `related_files`.
- Workspace archive safety tests are included because G33 reuses the same restore safety constraints: reject symlinks/preserved roots and never clear the target before snapshot validation succeeds.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/workspace_snapshot.py`
- `tests/coding_agent/action_safety/test_workspace_snapshot.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g33-snapshot-restore.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_workspace_snapshot.py tests/coding_agent/environment/test_workspace_archive.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_action_observability.py tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Workspace snapshot and workspace archive safety tests passed: 25 passed.
- Action observability, validation runner, and command policy regression tests passed: 20 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Local review:

- Local review found and G33 fixed two snapshot safety risks: restore now rejects snapshots stored inside the workspace before clearing, and snapshots now include a per-file path/size/SHA-256 manifest that rejects path or same-size content tampering.

Remaining risks:

- G33 is a local filesystem MVP only; it does not implement remote live sync, Docker sandboxing, or concurrent workspace reconciliation.
- Snapshot roots are caller-managed directories; lifecycle cleanup remains the caller's responsibility.

## G34 - Validation Feedback Context

Status: merged via PR #236.

### Before

Goal id: G34

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/validation_feedback.py`
- `tests/coding_agent/action_safety/test_validation_feedback.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g34-validation-feedback.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_validation_feedback.py tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Integration requires changing AgentKit pipeline/context stages.
- Integration changes context-pack authority semantics from ADR-0034.
- Feedback requires raw command output, raw commands, env values, prompts, messages, results, secrets, or file content.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G34 adds action-safety files and uses the existing context-pack model without changing context-system plugin behavior.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/validation_feedback.py`
- `tests/coding_agent/action_safety/test_validation_feedback.py`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g34-validation-feedback.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_validation_feedback.py tests/coding_agent/test_context_pack.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Validation feedback and context-pack tests passed: 9 passed.
- Validation runner and command policy regression tests passed: 14 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Local review:

- Local review found and G34 fixed two validation-feedback disclosure risks: validation labels are no longer rendered into context evidence, and failure summaries now only render typed numeric fields plus tightly bounded enum-like values.

Remaining risks:

- G34 provides a reference context adapter for validation feedback; it does not yet automatically inject feedback into live edit/command tool execution.
- Unsafe validation labels are intentionally omitted from rendered evidence rather than normalized, so callers should prefer stable safe labels when they want labels visible in context.

## G35 - Approval Routing

Status: merged via PR #237.

### Before

Goal id: G35

Intended files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/approval_routing.py`
- `tests/coding_agent/action_safety/test_approval_routing.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g35-approval-routing.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_approval_routing.py tests/approval/test_policy.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_command_policy.py tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Routing requires modifying approval coordinator/store lifecycle semantics.
- Routing would conflate denied actions with approval-required actions.
- Safe summaries require raw commands, raw paths, file content, env values, prompts, messages, results, or secrets.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G35 does not modify approval coordinator/store files.
- Because it defines approval routing semantics, G35 consulted PM-0011 and includes focused approval policy tests.

### After

Changed files:

- `src/coding_agent/action_safety/__init__.py`
- `src/coding_agent/action_safety/approval_routing.py`
- `tests/coding_agent/action_safety/test_approval_routing.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g35-approval-routing.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_approval_routing.py tests/approval/test_policy.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_command_policy.py tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Approval routing and approval policy tests passed: 16 passed.
- Command policy, safe edit, and patch plan regression tests passed: 21 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Local review:

- Local review found and G35 fixed two approval-routing risks: patch routing now requires a safe-edit decision and denies unsafe paths before risk routing, and file-edit routing now requires an explicit risk level so omitted risk cannot fail open.

Remaining risks:

- G35 defines routing semantics only; live tool execution still needs to call this route before mutation/execution.
- Existing approval coordinator/store behavior was intentionally not changed in this goal.

## G36 - End-to-End Safe Action Smoke

Status: merged via PR #238.

### Before

Goal id: G36

Intended files:

- `tests/coding_agent/action_safety/test_safe_action_smoke.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g36-e2e-smoke.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/ tests/coding_agent/tools/test_file_patch_tool.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Smoke coverage requires changing live tool execution, approval coordinator/store behavior, durable runtime semantics, or context-system authority semantics.
- Safe summaries require raw command output, patch content, file content, raw prompts, messages, results, secrets, or env values.
- Restore cannot be covered without snapshot roots outside the workspace.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G36 adds action-safety tests only and does not directly match `postmortem/index.yaml` production `related_files`.
- Because the smoke test exercises file patch and restore behavior, it includes existing focused patch and action-safety test suites in verification.

### After

Changed files:

- `tests/coding_agent/action_safety/test_safe_action_smoke.py`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `.opencode/prompts/tasks/action-safety-g36-e2e-smoke.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/ tests/coding_agent/tools/test_file_patch_tool.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Safe action smoke test passed: 1 passed.
- Action-safety and file patch tool regression tests passed: 55 passed.
- Context-system smoke test passed: 1 passed.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Local review:

- The first smoke attempt used `python -m py_compile pkg/app.py`; existing command policy denied it as path escape. G36 kept the production policy unchanged and switched the smoke validation command to the existing deterministic allow path, `python -c pass`, so the test covers command policy plus validation runner composition without broadening command execution.

Remaining risks:

- G36 is a deterministic composition smoke test; it still does not wire action-safety primitives into live autonomous execution paths.
- The smoke test intentionally uses an allowed validation command without file path arguments because path-bearing Python commands remain conservatively denied by the current command policy.

## G37 - Final Audit And Implementation Report

Status: merged via PR #239.

### Before

Goal id: G37

Intended files:

- `docs/action_safety/IMPLEMENTATION_REPORT.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `tests/coding_agent/action_safety/test_safe_action_smoke.py`
- `.opencode/prompts/tasks/action-safety-g37-final-audit.md`

Verification commands:

- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/ -k "action_safety or safe_edit or patch_plan or command_policy or validation_runner or workspace_snapshot" -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Stop criteria:

- Final audit discovers a failed acceptance criterion that cannot be fixed without changing durable runtime, context-system authority semantics, approval coordinator/store behavior, or live autonomous execution wiring.
- Verification requires full-repository ruff cleanup outside this phase.
- Safe audit/report evidence would require raw prompts, messages, results, secrets, command output, or file content.
- More than two fix iterations fail for the same reason.

Postmortem routing:

- G37 is report/audit scoped. It also tightens G36 smoke assertions based on local review feedback, so the G36 smoke test remains in final verification.

### After

Changed files:

- `docs/action_safety/IMPLEMENTATION_REPORT.md`
- `docs/action_safety/GOAL_PROGRESS.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `tests/coding_agent/action_safety/test_safe_action_smoke.py`
- `.opencode/prompts/tasks/action-safety-g37-final-audit.md`

Tests run:

- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/ -k "action_safety or safe_edit or patch_plan or command_policy or validation_runner or workspace_snapshot" -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`

Results:

- Strengthened safe action smoke test passed: 1 passed.
- ADR action-safety selector passed: 51 passed, 641 deselected.
- Context-system smoke test passed: 1 passed.
- Durable runtime smoke tests passed: 6 passed, 32 warnings from `slowapi` deprecation in dependencies.
- AgentKit build_context/runtime-stage span tests passed: 8 passed, 29 deselected.
- Scoped ruff format/check passed for `src/coding_agent/action_safety` and `tests/coding_agent/action_safety`.
- Diff whitespace check passed.

Local review:

- Late G36 local review found and G37 fixed three smoke-test assertion gaps: patch tool payloads are now included in no-leak checks, validation command strings are asserted absent from safe payloads, and patch/validation/restore spans now assert expected final safe metadata.

Remaining risks:

- G37 completes the phase-level implementation report and acceptance audit, but it does not wire action-safety primitives into every live autonomous execution path.
- Full-repository ruff remains outside this phase scope; G37 only ran scoped action-safety ruff checks.
