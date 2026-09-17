---
id: PM-0029
title: Keep volatile grounding out of the stable prompt prefix
status: active
severity: high
confidence: high
subsystems:
- agentkit
related_commits: []
related_files:
- src/agentkit/context/builder.py
- src/agentkit/runtime/pipeline.py
- tests/agentkit/context/test_builder.py
- tests/agentkit/test_incremental_context.py
release_checks:
- Run the focused context-builder and incremental-context tests before release.
- Review any new per-turn/per-call prompt content for volatility before giving it an early position.
---

# Summary

Per-call grounding blocks (runtime context with `elapsed_seconds`, active
approvals, runtime messages, plugin recall) are volatile by design. When they
were inserted before the last user message, single-instruction runs placed the
volatile block at message index ~1, invalidating the entire prompt prefix for
provider-side prompt caching (observed: ~7% cache hit vs 67-88% baselines).
Grounding is appended at the message tail so the conversation prefix stays
stable; the grounding block is part of the always-fresh tail either way.

# Trigger conditions

- Changes to `ContextBuilder.compose_messages`, `patch_messages`, or
  `grounding_insert_index`.
- Adding new fields to `_runtime_context_grounding` or new `build_context`
  hook output that varies between calls.
- Any feature that injects per-request content into an early message position.

# Known fix signals

- `grounding_insert_index` returns `len(core_messages)` (tail append).
- Requests across turns share a byte-identical prefix up to the newest tape
  entries; volatile blocks live only in the trailing region.
- Usage telemetry shows `cached_tokens` growing with conversation length.

# Release review checklist

- Run `uv run pytest tests/agentkit/context/test_builder.py tests/agentkit/test_incremental_context.py -q`.
- For any new grounding content, confirm it lands after all core messages, or
  that it is byte-stable across calls within a run.
