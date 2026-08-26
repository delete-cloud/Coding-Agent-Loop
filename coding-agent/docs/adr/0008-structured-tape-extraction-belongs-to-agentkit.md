# ADR-0008: Structured tape extraction belongs to agentkit

**Status**: Accepted
**Date**: 2026-04-14

## Context

Multiple consumers need to parse a flat tape entry stream into structured "turns" (user input → tool calls → final output). Today this logic is duplicated in at least three places with diverging semantics:

- `coding_agent.adapter.PipelineAdapter._current_turn_entries()` — filters `skip_context`, walks backward from tape end.
- `coding_agent.plugins.doom_detector.DoomDetectorPlugin._current_turn_tool_calls()` — uses `tape.snapshot()`, does **not** filter `skip_context`.
- `coding_agent.plugins.topic.TopicPlugin._extract_files_from_recent()` — finds last user message, extracts file paths from tool_call arguments.

A new requirement — offline evaluation of agent quality via DeepEval — needs the same turn extraction but also requires pairing `tool_call` ↔ `tool_result` by `tool_call_id` (since `tool_result` entries do not persist the tool `name`).

Placing this extraction in `coding_agent` would force the evaluation layer to import application code. Placing it in `agentkit` keeps it next to the data model it parses (`Tape`, `Entry`) and makes it available to any consumer.

## Decision

A read-only extraction module lives at `agentkit.tape.extract`. It operates on raw `tuple[Entry, ...]` from `tape.snapshot()` — not `TapeView`, which applies windowing/handoff logic unsuitable for full-history replay.

The extractor provides two visibility modes:

- **`visible`** (default) — skip entries with `meta.skip_context == True` when detecting turn boundaries and collecting tool calls. This prevents child subagent entries (which carry `skip_context: True` and `subagent_child: True`) from splitting a parent turn.
- **`raw`** — include all entries, for consumers that need to analyze hidden sub-flows.

The extractor does **not** expose `is_error` on tool call records because this field is not persisted in the tape (it only exists on the runtime `ToolResultEvent`).

DeepEval-specific adaptation (mapping `TurnTrace` → `LLMTestCase`) lives in `coding_agent.evaluation`, not in `agentkit`.

## Alternatives Rejected

- Put extraction in `coding_agent.evaluation` — rejected because non-eval consumers (doom detector, adapter) also need it and should not import evaluation code.
- Always include all entries (no skip_context filtering) and let consumers filter — rejected because turn boundary detection itself breaks on unfiltered child entries, making downstream filtering impossible.
- Extend `Entry`/tape format to persist `is_error` — deferred. Useful but constitutes a persistence format change with migration implications. Can be added later without breaking the extraction API.

## Acceptance Criteria

- [ ] `tests/agentkit/tape/test_extract.py::TestSkipContext::test_child_user_message_does_not_split_parent_turn`
- [ ] `tests/coding_agent/evaluation/test_adapter.py::TestGoldenFixtures::test_build_test_cases_uses_visible_extraction_by_default`
- [ ] `uv run pytest tests/agentkit/tape/test_extract.py tests/coding_agent/evaluation/test_adapter.py -v`

## References

- `src/agentkit/tape/extract.py` — new module
- `src/coding_agent/evaluation/adapter.py` — DeepEval adapter
- `src/coding_agent/tools/subagent.py` — where `skip_context` is injected
- `src/agentkit/context/builder.py` — existing `skip_context` filtering
