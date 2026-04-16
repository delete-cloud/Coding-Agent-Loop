# ADR-0006: Checkpoint plugin state restores as best-effort hints

**Status**: Accepted
**Date**: 2026-04-13

## Context

Some plugin state is cheap and useful to carry across checkpoint restore, but much of the runtime is not meaningfully serializable.

The restore flow therefore needs a narrow rule for plugin state that helps continuity without pretending to recreate a full live runtime.

## Decision

Checkpoint `plugin_states` restore as best-effort pre-mount hints.

The restore flow injects serialized plugin state into `ctx.plugin_states` before mount, using it as a hint that plugins may keep or overwrite during initialization. This is not a framework-level full runtime rehydration mechanism.

This narrow plugin-state rule is part of the current restore contract in `coding_agent`: it preserves useful continuity when state is cheap and serializable, without promising recovery of live runtime state such as shell processes, MCP connections, or in-memory caches.

## Alternatives Rejected

- Add a `StatefulPlugin` restore protocol in `agentkit` — too much framework coupling for an optional product concern.
- Require plugins to fully serialize and restore their live runtime state — unrealistic for shell sessions, MCP connections, and caches.
- Ignore plugin state entirely during restore — throws away useful continuity for state that is already cheap to persist safely.

## Acceptance Criteria

- [ ] `test_restore_injects_checkpoint_plugin_states_before_mount`
- [ ] `test_mount_can_overwrite_preloaded_plugin_state`
- [ ] `test_nonserializable_plugin_state_is_not_persisted`
- [ ] `uv run pytest tests/agentkit/checkpoint tests/ui/test_session_manager_runtime.py -k "plugin_state or setdefault or serializable" -v`

## References

- [`src/agentkit/runtime/pipeline.py`](../../src/agentkit/runtime/pipeline.py)
- [`src/coding_agent/plugins/shell_session.py`](../../src/coding_agent/plugins/shell_session.py)
- [`src/coding_agent/plugins/topic.py`](../../src/coding_agent/plugins/topic.py)
- [`src/coding_agent/plugins/memory.py`](../../src/coding_agent/plugins/memory.py)
- Archived design context: [`docs/specs/checkpoint-design.md`](../specs/checkpoint-design.md) and [`docs/specs/checkpoint-design-section2b.md`](../specs/checkpoint-design-section2b.md)
