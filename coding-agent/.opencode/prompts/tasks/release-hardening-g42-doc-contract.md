# G42 - Documentation Contract Checks

Add deterministic checks that keep release-facing docs aligned with executable command and boundary contracts.

Scope:

- Verify README `uv run python -m coding_agent ...` examples reference real Click commands.
- Verify release verification manifest pytest targets still exist in the repository.
- Verify README boundary summary for `agentkit` and `coding_agent` matches the packaged source layout.
- Do not change CLI behavior, runtime semantics, durable context behavior, action safety, or provider setup.

Verification:

- `uv run pytest tests/coding_agent/test_release_documentation_contract.py -v`
- `uv run pytest tests/coding_agent/test_release_verification_manifest.py tests/cli/test_entrypoint_contract.py tests/coding_agent/test_package_import_contract.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_release_documentation_contract.py`
- `uv run ruff check tests/coding_agent/test_release_documentation_contract.py`
- `git diff --check -- .`
