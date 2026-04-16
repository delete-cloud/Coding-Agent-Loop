# ADR-0011: Subagent timeout summaries distinguish partial progress from no-progress timeouts

**Status**: Proposed
**Date**: 2026-04-16

## Context

The `subagent` tool currently wraps child execution with `asyncio.wait_for()` and always appends any child trace entries back into the parent tape, even when the child turn times out.

That behavior preserves useful evidence, but the returned timeout summary is currently a single generic string. In practice this makes three materially different outcomes look the same to users and to downstream debugging sessions: the child completed normally, the child timed out after recording visible progress, and the child timed out before recording any visible progress.

This ambiguity is especially costly in concurrent subagent experiments and approval-heavy flows, because the parent transcript may show child tool activity while the final tool result still only says `timed out`. Future readers will ask why the tool preserves child trace on timeout and how to interpret that outcome, so the behavior needs an explicit decision record.

## Decision

Keep the existing v1 `subagent` tool return format as a human-readable string, but make timeout summaries distinguish whether any child progress was recorded before timeout.

In v1:

- normal completion continues to use the existing completed-path summary behavior
- timeout with appended child trace entries must return a timeout summary that explicitly says partial progress was recorded
- timeout with no appended child trace entries must return a timeout summary that explicitly says no child progress was recorded

The timeout path will continue to append child trace entries to the parent tape when they exist. This ADR does not introduce a new structured result schema, new wire protocol fields, or new HTTP/UI payload shapes.

## Alternatives Rejected

- Keep one generic timeout string for all timeout outcomes — rejected because it preserves the current ambiguity and makes debugging concurrent child runs unnecessarily confusing.
- Drop child trace on timeout so the generic timeout string becomes technically consistent — rejected because it throws away useful evidence and makes debugging child failures harder.
- Change the `subagent` tool to return structured JSON immediately — rejected for this slice because it expands the behavior change into a broader tool-contract and protocol discussion that deserves its own scoped decision.

## Implementation Plan

- Touch `src/coding_agent/tools/subagent.py` only for the reporting logic and any minimal helper extraction needed to determine whether child entries beyond the base length were appended.
- Keep `_append_child_trace_to_parent(...)` semantics unchanged in this slice.
- Base the timeout summary on evidence already available in the child tape / appended-entry boundary; do not add new persisted metadata or wire fields.
- Add focused regression coverage in `tests/coding_agent/tools/test_subagent.py` for:
  - timeout with partial child progress
  - timeout with no recorded child progress
  - completed-path behavior remaining stable
- Do not change timeout config defaults, approval flows, TUI rendering, or HTTP schemas in this ADR’s implementation.

## Acceptance Criteria

- [ ] `test_subagent_timeout_summary_reports_partial_progress_when_child_entries_are_recorded`
- [ ] `test_subagent_timeout_summary_reports_no_progress_when_no_child_entries_are_recorded`
- [ ] `test_subagent_timeout_summary_keeps_completed_path_unchanged`
- [ ] `uv run pytest tests/coding_agent/tools/test_subagent.py -k "partial_progress or no_progress or keeps_completed_path_unchanged" -v`

## References

- `src/coding_agent/tools/subagent.py`
- `tests/coding_agent/tools/test_subagent.py`
- `src/coding_agent/app.py`
- `docs/adr/README.md`
