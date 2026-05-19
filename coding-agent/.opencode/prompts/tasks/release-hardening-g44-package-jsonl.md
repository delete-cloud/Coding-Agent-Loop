# G44 - Packaging And JSONL Compatibility Smoke

Add deterministic release smoke tests for package metadata and JSONL tape compatibility.

Scope:

- Verify packaged wheel metadata still includes both `agentkit` and `coding_agent`.
- Verify the `coding-agent` console script still points at the Click entrypoint.
- Verify JSONL tape/store compatibility for legacy handoff anchors, current anchors, append/load behavior, and fold-boundary anchors.
- Do not change storage semantics, tape schemas, package metadata, runtime behavior, or CLI behavior unless a smoke test exposes a real regression.

Verification:

- `uv run pytest tests/coding_agent/test_release_package_jsonl_contract.py -v`
- `uv run pytest tests/agentkit/tape/ tests/coding_agent/plugins/test_storage.py tests/coding_agent/test_package_import_contract.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_release_package_jsonl_contract.py`
- `uv run ruff check tests/coding_agent/test_release_package_jsonl_contract.py`
- `git diff --check -- .`
