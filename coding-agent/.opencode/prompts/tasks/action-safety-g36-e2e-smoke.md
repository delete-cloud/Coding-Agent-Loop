# G36 - End-to-End Safe Action Smoke

Add a deterministic smoke test that composes the action-safety primitives delivered in G27-G35.

Scope:

- Cover patch planning, safe edit validation, file patch dry-run/apply, command policy, validation runner, action observability, approval routing, and workspace snapshot/restore in one temporary workspace flow.
- Keep this as a smoke/regression test; do not wire live tools, approvals, or runtime pipeline behavior in this goal.
- Keep summaries and observability metadata bounded and free of raw command output, patch content, file content, secrets, prompts, messages, results, and text payloads.
- Update ADR-0035 and the phase ledger with verification evidence.

Verification:

- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/ tests/coding_agent/tools/test_file_patch_tool.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `uv run ruff check src/coding_agent/action_safety tests/coding_agent/action_safety`
- `git diff --check -- .`
