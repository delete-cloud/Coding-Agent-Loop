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

## [2026-04-10] Closure decisions

1. Keep HTTP session creation defaulting to the real provider path (`session.provider is None`) because that repository contract is already explicitly tested.
2. Fix the streaming integration failure at the test boundary, not in production code, because the failure was caused by non-hermetic test setup rather than broken SSE or HookSpec runtime enforcement logic.
3. Use `MockProvider()` only inside `tests/integration/test_wire_http_integration.py::test_prompt_streaming_events` so the test verifies deterministic `StreamDelta` emission without weakening the HTTP default-provider contract.
