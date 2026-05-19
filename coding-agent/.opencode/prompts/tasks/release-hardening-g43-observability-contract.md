# G43 - Observability Safety Contract

Add deterministic release-safety checks for observability metadata and trace/span attributes.

Scope:

- Verify OTLP export drops sensitive attribute keys and raw error messages.
- Verify runtime trace metadata exports only safe correlation identifiers.
- Verify representative action-safety, retrieval, and context-pack attribute factories produce metadata-only keys and values.
- Do not change durable runtime semantics, context-system semantics, action-safety behavior, or exporter configuration behavior.

Verification:

- `uv run pytest tests/coding_agent/test_release_observability_contract.py -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/action_safety/test_action_observability.py tests/coding_agent/plugins/test_kb_plugin.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/coding_agent/test_release_observability_contract.py`
- `uv run ruff check tests/coding_agent/test_release_observability_contract.py`
- `git diff --check -- .`
