# ADR-0001: Checkpoint captures serialized tape and plugin state

**Status**: Accepted
**Date**: 2026-04-13

## Context

Checkpoint needs a framework-level snapshot contract that works for any `agentkit` consumer, not just `coding_agent`.

The snapshot must be self-contained enough for restore, but it must not couple checkpointing to live plugin instances or product-specific runtime state.

## Decision

Checkpoint snapshots capture serialized tape entries, a JSON-safe subset of `ctx.plugin_states`, and caller-provided JSON-safe `extra` metadata.

The framework checkpoint layer reads state from `PipelineContext`, stays event-driven, does not snapshot external environment state, and does not introduce a framework-level plugin restore protocol.

## Alternatives Rejected

- Snapshot live plugin instances or `PluginRegistry` state — runtime objects do not have a stable, reusable serialized form.
- Snapshot external environment state in `agentkit` — git refs, sandbox state, and MCP processes are product-layer concerns.
- Add a framework-level `StatefulPlugin` restore protocol — too much framework surface for an optional product-level need.
- Store only a bookmark with no tape copy — smaller, but not self-contained for restore.

## Acceptance Criteria

- [ ] `test_capture_serializes_tape_entries_and_json_safe_plugin_states`
- [ ] `test_capture_rejects_non_json_safe_extra`
- [ ] `test_checkpoint_store_lists_meta_without_loading_entries`
- [ ] `uv run pytest tests/agentkit/checkpoint -v`

## References

- [`src/agentkit/runtime/pipeline.py`](../../src/agentkit/runtime/pipeline.py)
- [`src/agentkit/storage/protocols.py`](../../src/agentkit/storage/protocols.py)
- [`src/agentkit/directive/types.py`](../../src/agentkit/directive/types.py)
- Archived design context: [`docs/specs/checkpoint-design.md`](../specs/checkpoint-design.md)
