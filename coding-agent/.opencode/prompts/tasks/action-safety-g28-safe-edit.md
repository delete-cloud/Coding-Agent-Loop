Goal:
Add the G28 safe file edit policy for path, size, symlink, binary, and workspace-boundary validation.

Scope:
- Add a Coding Agent app-level safe edit validator.
- Return bounded decision/reason values without file content.
- Validate existing text files, missing/create targets, workspace escapes, symlinks, directories, binary files, and oversized files.
- Add deterministic focused tests for the ADR-0035 G28 criterion.
- Update ADR-0035 and the phase ledger.

Out of scope:
- Integrating the validator into `file_write`, `file_replace`, or `file_patch`.
- Patch dry-run/preview behavior.
- Command policy, validation runner, observability, approval routing, or snapshot/restore.
- AgentKit pipeline changes.

Context:
- ADR:
  - `docs/adr/0035-action-safety-and-workspace-execution.md`
- Relevant files:
  - `src/coding_agent/action_safety/safe_edit.py`
  - `src/coding_agent/action_safety/__init__.py`
  - `tests/coding_agent/action_safety/test_safe_edit.py`
  - `tests/coding_agent/action_safety/test_patch_plan.py`

Postmortem routing:
- G28 adds/updates action-safety files and does not modify existing postmortem `related_files`.
- Later goals that integrate policy into `src/coding_agent/tools/file_ops.py`, `src/coding_agent/tools/file_patch_tool.py`, or `src/coding_agent/plugins/core_tools.py` must apply PM-0001/PM-0009 focused checks.

Target tests:
- `uv run pytest tests/coding_agent/action_safety/test_safe_edit.py tests/coding_agent/action_safety/test_patch_plan.py -v`
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
- Stop if implementation requires patch apply/dry-run integration from G29.
- Stop if implementation requires AgentKit pipeline changes.
