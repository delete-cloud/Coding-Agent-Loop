# G40 - Package Import Contract

Add package/import contract smoke tests for the release-hardening phase.

Scope:

- Verify public `agentkit` imports stay framework-owned and do not directly import `coding_agent`.
- Verify importing top-level `coding_agent` does not require provider credentials or heavy KB/token dependencies.
- Verify the wheel package configuration continues to include both `src/coding_agent` and `src/agentkit`.
- Do not change package behavior unless a test exposes a narrow import-contract bug.

Verification:

- `uv run pytest tests/coding_agent/test_package_import_contract.py tests/coding_agent/test_bootstrap.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_package_import_contract.py`
- `uv run ruff check tests/coding_agent/test_package_import_contract.py`
- `git diff --check -- .`
