Goal:
Add G29 patch application dry-run/preview validation and deterministic failure reporting.

Scope:
- Keep existing `file_patch(path, patch)` behavior compatible.
- Add an optional `dry_run` mode that validates safe edit policy, patch plan parsing, and hunk context without mutating the file.
- Return deterministic JSON failures for policy, parse, and context errors.
- Add focused tests proving dry-run does not mutate on success or failure.
- Update ADR-0035 and the phase ledger.

Out of scope:
- Multi-file patch transactions.
- Cloud `file_patch` policy integration.
- Command policy, validation runner, observability, approval routing, or snapshot/restore.
- AgentKit pipeline changes.

Context:
- ADR:
  - `docs/adr/0035-action-safety-and-workspace-execution.md`
- Relevant files:
  - `src/coding_agent/tools/file_patch_tool.py`
  - `src/coding_agent/action_safety/patch_plan.py`
  - `src/coding_agent/action_safety/safe_edit.py`
  - `tests/coding_agent/tools/test_file_patch_tool.py`
  - `tests/coding_agent/action_safety/test_patch_plan.py`
  - `tests/coding_agent/action_safety/test_safe_edit.py`

Postmortem routing:
- `src/coding_agent/tools/file_patch_tool.py` is an existing core tool surface.
- PM-0001/PM-0009 do not list this exact file, but their focused tool-control-flow checks apply by analogy because patch execution is registered through `CoreToolsPlugin`.

Target tests:
- `uv run pytest tests/coding_agent/tools/test_file_patch_tool.py tests/coding_agent/action_safety/test_patch_plan.py tests/coding_agent/action_safety/test_safe_edit.py -v`
- `uv run pytest tests/coding_agent/environment/test_local_environment.py tests/agentkit/environment/test_protocols.py tests/coding_agent/plugins/test_core_tools_parity.py -v`
- `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_shell.py tests/approval/test_policy.py tests/coding_agent/environment/test_cloud_environment.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety src/coding_agent/tools/file_patch_tool.py tests/coding_agent/action_safety tests/coding_agent/tools/test_file_patch_tool.py`
- `uv run ruff check src/coding_agent/action_safety src/coding_agent/tools/file_patch_tool.py tests/coding_agent/action_safety tests/coding_agent/tools/test_file_patch_tool.py`
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
- Stop if implementation requires multi-file transactions or command policy.
- Stop if implementation requires AgentKit pipeline changes.
