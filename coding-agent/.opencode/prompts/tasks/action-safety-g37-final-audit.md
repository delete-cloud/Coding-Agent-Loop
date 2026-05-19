# G37 - Final Action Safety Audit

Finish the Action Safety + Workspace Execution phase.

Scope:

- Produce `docs/action_safety/IMPLEMENTATION_REPORT.md`.
- Audit ADR-0035 acceptance criteria and mark final verification criteria only after running them.
- Run target action-safety, context, durable/runtime, and scoped formatting checks.
- Address late local review feedback from G36 if it is test/audit scoped.
- Do not change live autonomous execution wiring, approval coordinator/store behavior, durable runtime semantics, or context-system authority semantics.

Verification:

- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/ -k "action_safety or safe_edit or patch_plan or command_policy or validation_runner or workspace_snapshot" -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`
