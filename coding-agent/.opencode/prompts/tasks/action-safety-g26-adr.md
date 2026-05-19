Goal:
Create the Action Safety + Workspace Execution boundary ADR for G26.

Scope:
- Record ownership boundaries for action safety, workspace execution, validation, observability, approval risk routing, and snapshot/restore.
- Explicitly split local and cloud execution behavior so later goals do not assume local shell/file guards apply to cloud tools.
- Define executable acceptance criteria that G27-G37 will satisfy.
- Update the phase ledger before and after implementation.

Out of scope:
- Production code changes.
- Implementing patch planning, safe edit policy, command policy, validation runner, observability, approval routing, or snapshot/restore.
- Changing AgentKit pipeline behavior.
- Changing durable runtime or context-system semantics.

Context:
- ADRs:
  - `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md`
  - `docs/adr/0017-cloud-workspace-execution.md`
  - `docs/adr/0022-runtime-profiles-and-sandbox-policy.md`
  - `docs/adr/0028-observability-and-langfuse-integration.md`
  - `docs/adr/0034-context-system-boundaries-and-evidence.md`
- Current-state docs:
  - `docs/action_safety/CURRENT_STATE.md`
  - `docs/action_safety/GOAL_PROGRESS.md`
- Relevant code surfaces:
  - `src/coding_agent/environment/local.py`
  - `src/coding_agent/environment/cloud.py`
  - `src/coding_agent/tools/file_ops.py`
  - `src/coding_agent/tools/file_patch_tool.py`
  - `src/coding_agent/tools/shell.py`
  - `src/coding_agent/tools/sandbox.py`
  - `src/coding_agent/approval/policy.py`
  - `src/coding_agent/plugins/core_tools.py`
  - `src/coding_agent/workspace_archive.py`

Postmortem routing:
- G26 is docs-only.
- Future goals touching `src/coding_agent/tools/file_ops.py`, `src/coding_agent/tools/shell.py`, or `src/coding_agent/plugins/core_tools.py` must account for PM-0001 and PM-0009 focused checks.

Target tests:
- `test -f docs/adr/0035-action-safety-and-workspace-execution.md`
- `test -f .opencode/prompts/tasks/action-safety-g26-adr.md`
- `rg -n "## Context|## Decision|## Alternatives Rejected|## Acceptance Criteria|## References" docs/adr/0035-action-safety-and-workspace-execution.md`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_workspace_archive.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
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
- Stop if the ADR would require an AgentKit pipeline rewrite.
- Stop if the ADR would require changing durable runtime or context-system semantics in G26.
