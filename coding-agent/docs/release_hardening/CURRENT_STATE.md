# Release Hardening Current State

Date: 2026-05-20
Baseline: `main` at `6b25a25b6bb25131939331095622a3be3f4ba1ad` or newer.

This document records the release-quality baseline before G39-G45. It is intentionally descriptive; code and tests remain the source of truth.

## Completed Foundation

- Durable Runtime G00-G11 is complete and documented under `docs/durable_runtime/`.
- Context System + Evaluation G12-G24 is complete and documented under `docs/context_system/`.
- Action Safety + Workspace Execution G25-G37 is complete and documented under `docs/action_safety/`.
- ADR-0034 and ADR-0035 define the current context-system and action-safety boundaries.

## Repository And Packaging Shape

- The project is a Python package using `uv`, `hatchling`, and Python `>=3.12`.
- `pyproject.toml` builds both `src/coding_agent` and `src/agentkit` into one wheel.
- The `coding-agent` console script points at `coding_agent.__main__:main`.
- `README.md` describes the current REPL, run, and HTTP entrypoints, plus the `agentkit`/`coding_agent` boundary.

## Current Release Checks

The cross-phase regression baseline from the release-hardening goal is:

- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`

Additional targeted checks already used by prior phases include scoped action-safety ruff checks, context-system plugin tests, and durable runtime persistence tests. Full-repository ruff is known to require care because broad cleanup can create large unrelated diffs.

## Contract Surfaces To Stabilize

Release hardening should focus on contracts that are easy to regress while iterating:

- Package/import contract: `agentkit` remains reusable framework code and `coding_agent` remains the app layer.
- CLI contract: help/entrypoint commands should remain importable and deterministic without provider credentials.
- Verification contract: release checks should be discoverable and runnable without real external services.
- Documentation contract: README and phase reports should point to commands and boundaries that match the current code.
- Observability/safety contract: no release-hardening change should add raw prompt, content, message, result, secret, or text values to trace/span attributes.
- Persistence compatibility contract: release checks must preserve JSONL compatibility and durable runtime semantics.

## Non-Goals

- Do not rewrite AgentKit Core.
- Do not change durable runtime, context system, or action-safety semantics.
- Do not add schedules, desktop, bridge, proactive agent behavior, or full Docker sandboxing.
- Do not require production credentials, external services, or real LLM calls.
- Do not mix large full-repository formatting cleanup with behavior or contract changes.

## Initial Gaps

- No `docs/release_hardening/` ledger or implementation report exists yet.
- No central release verification manifest exists for the preserved regression baseline.
- Import/entrypoint contract coverage is scattered across existing tests and not documented as release gates.
- README command snippets are not currently tied to a deterministic docs/contract check.
- Full-repo formatting status is not a release gate because fixing it may be too large for this phase.
