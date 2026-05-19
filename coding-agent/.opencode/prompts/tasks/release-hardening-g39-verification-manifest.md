# G39 - Release Verification Manifest

Add a central release verification manifest for the preserved regression baseline.

Scope:

- Add `docs/release_hardening/release-verification.yaml`.
- Add a deterministic loader under `src/coding_agent/verification/`.
- Add focused tests that prove the manifest contains the preserved baseline commands and rejects malformed local fixtures.
- Do not change existing task-packet verification semantics from ADR-0007.
- Do not execute the full release manifest in this goal; execution remains explicit through the listed commands.

Verification:

- `uv run pytest tests/coding_agent/test_release_verification_manifest.py -v`
- `uv run pytest tests/coding_agent/test_verification.py tests/cli/test_verify.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/verification tests/coding_agent/test_release_verification_manifest.py`
- `uv run ruff check src/coding_agent/verification tests/coding_agent/test_release_verification_manifest.py`
- `git diff --check -- .`
