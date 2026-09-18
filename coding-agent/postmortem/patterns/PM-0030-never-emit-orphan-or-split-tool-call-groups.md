---
id: PM-0030
title: Never emit orphan tool results or split tool-call groups
status: active
severity: high
confidence: high
subsystems:
- agentkit
- coding_agent
related_commits: []
related_files:
- src/agentkit/runtime/pipeline.py
- src/agentkit/context/builder.py
- src/coding_agent/plugins/summarizer.py
- src/coding_agent/providers/openai_compat.py
- tests/agentkit/runtime/test_pipeline.py
- tests/coding_agent/plugins/test_summarizer.py
- tests/providers/test_openai_compat.py
release_checks:
- Run the summarizer, pipeline, and openai_compat provider tests before release.
- For any new context-window/fold mechanism, prove the boundary cannot open
  inside a tool_call/tool_result group.
---

# Summary

Strict providers (kimi `api.kimi.com/coding`) reject request history when a
`tool` message references an id absent from all assistant `tool_calls`
(`tool_call_id  is not found`) or when an assistant `tool_calls` message is
not immediately followed by results for every call. Three defects could
produce those wire shapes:

1. `SummarizerPlugin.resolve_context_window` chose a raw entry index
   (`len - keep_recent`) that could open the visible window inside a
   tool_call/tool_result group, orphaning results whose calls were folded.
   Observed killing eval trials at ~30 model calls (~100 tape entries).
2. The pipeline appended `tool_call` and rejection `tool_result` entries
   per-call in one loop, so a mid-list invalid/rejected call interleaved a
   result inside the call group, yielding adjacent assistant tool_calls
   messages on the wire.
3. `openai_compat` captured a streaming tool call's `id` only on the first
   delta per index; providers emitting the id on a later delta (or never)
   produced empty `tool_call_id`s.

Fixes: `_group_safe_split` backs any window boundary off to the start of the
enclosing tool-call group; all `tool_call` entries are appended before any
rejection results so tape groups stay contiguous; stream accumulation updates
`id` on every delta and synthesizes `call_<uuid>` when none ever arrives.

# Trigger conditions

- Changes to `resolve_context_window`/`handoff`/window start computation.
- Changes to where `tool_call`/`tool_result` entries are appended in the
  pipeline.
- Changes to streaming tool-call delta accumulation.
- Adding a new fold/summarization/checkpoint mechanism that can drop or
  reorder tape entries.

# Known fix signals

- Every `tool` message's `tool_call_id` appears in a preceding assistant
  `tool_calls` entry on the wire.
- Tape order per round: all `tool_call` entries contiguous, then all
  `tool_result` entries.
- Windowed entry slices never begin with `tool_result` or mid tool_call run.

# Release review checklist

- Run `uv run pytest tests/coding_agent/plugins/test_summarizer.py tests/agentkit/runtime/test_pipeline.py tests/providers/test_openai_compat.py -q`.
- For any new fold mechanism, construct a tape whose raw boundary lands inside
  a tool group and verify the emitted message list has no orphan results.
