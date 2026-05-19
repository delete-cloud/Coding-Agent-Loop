Goal:
Record the current Action Safety + Workspace Execution baseline and create the phase ledger for G25-G37.

Scope:
- Document current file, patch, shell, sandbox, approval, core tool wiring, and workspace archive entrypoints.
- Define the sequential G25-G37 goal map because no existing repository document defines those goal ids.
- Create the task packet and progress ledger that later goals will update before and after implementation.

Out of scope:
- Production code changes.
- ADR creation for G25.
- File edit, command policy, validation runner, observability, or snapshot/restore behavior changes.
- Durable runtime or context-system semantic changes.

Context:
- ADRs:
  - `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md`
  - `docs/adr/0017-cloud-workspace-execution.md`
  - `docs/adr/0022-runtime-profiles-and-sandbox-policy.md`
- Relevant files:
  - `src/coding_agent/tools/file_ops.py`
  - `src/coding_agent/tools/file_patch_tool.py`
  - `src/coding_agent/tools/shell.py`
  - `src/coding_agent/tools/sandbox.py`
  - `src/coding_agent/environment/local.py`
  - `src/coding_agent/environment/cloud.py`
  - `src/coding_agent/approval/policy.py`
  - `src/coding_agent/approval/coordinator.py`
  - `src/coding_agent/plugins/core_tools.py`
  - `src/coding_agent/workspace_archive.py`
  - `src/coding_agent/environment/workspace_provider.py`
  - `tests/coding_agent/environment/test_local_environment.py`
  - `tests/coding_agent/environment/test_cloud_environment.py`
  - `tests/coding_agent/tools/test_file_ops.py`
  - `tests/coding_agent/tools/test_shell.py`
  - `tests/approval/test_policy.py`
  - `tests/coding_agent/environment/test_workspace_archive.py`

Postmortem routing:
- `postmortem/index.yaml` matches PM-0001 and PM-0009 for `src/coding_agent/tools/file_ops.py`, `src/coding_agent/tools/shell.py`, and `src/coding_agent/plugins/core_tools.py`.
- Local and cloud environments have separate tool implementations; later goals must verify both when changing shared action-safety policy.
- G25 is docs-only.
- Later goals touching those files must include focused tests for the affected tool paths and control-flow review before release.

Target tests:
- `test -f docs/action_safety/GOAL_PROGRESS.md`
- `test -f docs/action_safety/CURRENT_STATE.md`
- `test -f .opencode/prompts/tasks/action-safety-g25-current-state.md`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_workspace_archive.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Loop policy:
- Engineer implements the smallest correct documentation change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
- Stop if current file, patch, shell, approval, and workspace archive entrypoints cannot be identified.
- Stop if a docs-only current-state audit requires production runtime changes.
