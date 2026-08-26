# ADR-0004: Cold restore provides degraded continuity

**Status**: Accepted
**Date**: 2026-04-13

## Context

Cold recovery after process restart, pod drift, or cache eviction can rebuild persisted conversation state, but it cannot reliably recreate every live runtime object.

Trying to promise full runtime restoration would overstate what the current plugin and process model can safely recover.

## Decision

Cold restore guarantees conversation continuity, not full runtime continuity.

Recovered state includes persisted tape history and other state that can be rebuilt from persisted data. Live runtime state such as shell cwd and env, MCP connections, plugin caches, and in-memory metrics accumulators is not restored; plugins reinitialize through normal mount.

## Alternatives Rejected

- Promise full runtime restoration — not realistic for the current plugin model.
- Fail cold restore unless every plugin can fully rehydrate — makes recovery too brittle to be useful.
- Hide degraded recovery behind implicit fallbacks — makes the boundary misleading for future implementers and users.

## Acceptance Criteria

- [ ] `test_cold_restore_recovers_conversation_history`
- [ ] `test_cold_restore_does_not_restore_live_shell_state`
- [ ] `test_cold_restore_reinitializes_plugins_via_mount`
- [ ] `uv run pytest tests/ui/test_session_manager_runtime.py -k "cold_restore or shell_state or reinitialize" -v`

## References

- [`src/coding_agent/ui/session_manager.py`](../../src/coding_agent/ui/session_manager.py)
- [`src/coding_agent/plugins/shell_session.py`](../../src/coding_agent/plugins/shell_session.py)
- [`src/coding_agent/plugins/mcp.py`](../../src/coding_agent/plugins/mcp.py)
- [`src/agentkit/runtime/pipeline.py`](../../src/agentkit/runtime/pipeline.py)
- Archived design context: [`docs/specs/checkpoint-design-section2a.md`](../specs/checkpoint-design-section2a.md) and [`docs/specs/checkpoint-design-section2b.md`](../specs/checkpoint-design-section2b.md)
