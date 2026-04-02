# P4 Decisions

## [2026-03-31] Session Start

### Architecture Decisions (from plan review)
1. Two-layer defense: HookRuntime (primary) + Pipeline (defense-in-depth)
2. HookTypeError extends HookError (keeps hook_name attribute)
3. None return is always allowed even when return_type is set (means "no opinion")
4. Observer hooks (on_error, on_checkpoint, on_session_event) do NOT get return_type validation
5. execute_tools_batch gets no return_type (return shape too dynamic)
6. approve_tool_call fail-closed = return Reject() on bad type, not skip
7. resolve_context_window guard: tuple + len==2 + isinstance(first, int) structural check
8. on_turn_end guard: filter-before-store (drop bad items, log warning, continue)
