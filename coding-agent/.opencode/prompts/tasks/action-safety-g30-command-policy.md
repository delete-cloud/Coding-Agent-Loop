Goal:
Add the G30 command execution policy model for allow, deny, approval, cwd/env, timeout, and path escape risk.

Scope:
- Add a Coding Agent app-level command policy model.
- Return bounded allow/deny/approval decisions and reason codes.
- Cover local and cloud environment kinds through the same policy contract.
- Validate shell syntax, cwd escape, path escape, env key safety, timeout limit, destructive commands, network commands, unknown commands, and validation commands.
- Update ADR-0035 and the phase ledger.

Out of scope:
- Wiring command policy into `bash_run`.
- Validation runner execution.
- Approval coordinator integration.
- Observability spans/events.
- AgentKit pipeline changes.

Context:
- ADR:
  - `docs/adr/0035-action-safety-and-workspace-execution.md`
- Relevant files:
  - `src/coding_agent/action_safety/command_policy.py`
  - `src/coding_agent/action_safety/__init__.py`
  - `tests/coding_agent/action_safety/test_command_policy.py`
  - `src/coding_agent/tools/shell.py`
  - `src/coding_agent/environment/cloud.py`

Postmortem routing:
- G30 adds action-safety files and does not modify existing postmortem `related_files`.
- Later integration into `src/coding_agent/tools/shell.py` or `src/coding_agent/plugins/core_tools.py` must apply PM-0001/PM-0009 focused checks.

Target tests:
- `uv run pytest tests/coding_agent/action_safety/test_command_policy.py tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
- `uv run pytest tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py tests/coding_agent/environment/test_local_environment.py -v`
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
- Stop if implementation requires `bash_run` execution integration or approval routing.
- Stop if implementation requires AgentKit pipeline changes.
