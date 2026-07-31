---
id: PM-0026
title: Never persist blank exception messages as run errors
status: active
severity: high
confidence: high
subsystems:
- runtime
- adapter
related_commits: []
related_files:
- src/coding_agent/adapter/types.py
- src/coding_agent/adapter/pipeline.py
- src/coding_agent/runs/lifecycle.py
- src/coding_agent/tools/subagent.py
- src/coding_agent/stores/runtime_store.py
- tests/coding_agent/test_adapter_types.py
- tests/coding_agent/test_pipeline_adapter.py
- tests/coding_agent/test_run_lifecycle.py
- tests/coding_agent/test_sqlite_runtime_store.py
- tests/coding_agent/test_jsonl_runtime_store.py
release_checks:
- Run focused tests for adapter and run lifecycle changes before release.
- Grep new code for bare `str(exc)` flowing into TurnOutcome.error or run
  record writes; require exception_error_message (or equivalent fallback).
---

# Summary

An exception with an empty `str()` (e.g. bare `RuntimeError()`, or a
`PipelineError(str(exc))` wrapper around one) produced `TurnOutcome.error == ""`.
The runtime store's `_require_non_empty("error", ...)` invariant then raised
`ValueError("error must be non-empty")` during `update_agent_run`, masking the
real provider failure (observed live: codex OAuth refresh 403 surfaced only as
the masking ValueError).

# Trigger Conditions

- Any exception with empty/whitespace-only `str()` escaping a pipeline stage
  or turn error handler (provider-agnostic, not codex-specific).
- Run/turn error persistence via `RuntimeTurnFinalizer.complete` ->
  `finish_run` -> `update_agent_run`.

# Known Fix Signals

- `exception_error_message()` in `src/coding_agent/adapter/types.py` falls
  back to the exception type name (plus chained cause) when `str(exc)` is
  blank; used at all `TurnOutcome.error` / run-error construction sites.
- `_normalize_optional_error()` in `src/coding_agent/stores/runtime_store.py`
  normalizes blank error strings to None at the `update_agent_run` write
  boundary (JSONL/SQLite/PG) as defense-in-depth.

# Release Review Checklist

- Run focused tests for adapter and run lifecycle changes before release.
- Grep new code for bare `str(exc)` flowing into TurnOutcome.error or run
  record writes; require exception_error_message (or equivalent fallback).
