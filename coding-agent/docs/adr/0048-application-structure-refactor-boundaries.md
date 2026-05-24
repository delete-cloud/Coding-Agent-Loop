# ADR-0048: Application structure refactor boundaries

**Status**: Accepted
**Date**: 2026-05-25

## Context

`src/coding_agent` has accumulated several product concerns in top-level modules and in broadly named packages. The largest examples are `__main__.py`, which mixes the Click entrypoint with remote, KB, verification, and server command implementations, and `ui/`, which contains both terminal presentation code and HTTP server/runtime session management.

This makes ownership harder to infer from paths. It also increases review risk because moving one command or server concern often requires editing very large files that contain unrelated behavior.

## Decision

Refactor the application package by moving code toward explicit product-area packages while preserving behavior and import compatibility during the transition.

The first implementation slice will split CLI command implementation out of `coding_agent.__main__` into focused modules under `coding_agent.cli`. `coding_agent.__main__` remains the executable module and keeps compatibility exports for `create_agent` and `create_child_pipeline`.

Later slices may move HTTP server/runtime code out of `coding_agent.ui`, and collect Bee and Topic modules into dedicated packages. Those later moves should keep old import paths as compatibility shims until all in-repo imports and tests have moved.

## Alternatives Rejected

- Keep the current flat structure — rejected because root-level prefixes such as `bee_*` and `topic_*` already encode hidden package boundaries without giving import-level ownership.
- Move all domains in one large refactor — rejected because it would create a high-risk import churn diff across CLI, server, runtime, and tests.
- Rename behavior while moving files — rejected because the current goal is structural clarity without changing command behavior.

## Acceptance Criteria

- [x] `test_entrypoint_contract`
- [x] `test_commands`
- [x] `test_kb_commands`
- [x] `test_postmortem`
- [x] `test_remote_client`
- [x] `test_verify`
- [x] `uv run pytest tests/cli/ -v`
- [x] `uv run pytest tests/coding_agent/test_bootstrap.py -v`

## References

- `src/coding_agent/__main__.py`
- `src/coding_agent/cli/`
- `docs/CODING-AGENT-ARCHITECTURE.md`
- `.opencode/prompts/tasks/coding-agent-structure-cli-entrypoint.md`
