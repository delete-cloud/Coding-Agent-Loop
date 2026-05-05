Goal:
Preserve ADR-0018 owner-fencing failures through the adapter and HTTP prompt surface so stale-owner errors do not degrade into generic assistant error turns.

Scope:
- Re-raise `SessionOwnershipConflictError` from `PipelineAdapter.run_turn()` instead of converting it into `TurnOutcome(ERROR)`.
- Preserve prompt-time owner conflicts in `SessionManager.run_agent()` as fatal errors while still cleaning up runtime state.
- Keep `/sessions/{session_id}/prompt` behavior consistent with the streaming surface: pre-turn ownership conflicts stay HTTP 404/409, while mid-turn ownership conflicts surface as SSE `Error` events instead of fake assistant `StreamDelta` + `TurnEnd(ERROR)` output.

Out of scope:
- Do not change pre-turn owner authorization or event-stream ownership behavior.
- Do not add distributed child-worker coordination or new ADR scope.
- Do not refactor unrelated HTTP/session-manager error handling.

Context:
- ADRs:
  - `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
  - `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
- Relevant files:
  - `src/coding_agent/adapter.py`
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/coding_agent/test_pipeline_adapter.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/coding_agent/test_pipeline_adapter.py -k "ownership_conflict or stale_owner" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "ownership_conflict or stale_owner" -v`
- `uv run pytest tests/ui/test_http_server.py -k "stale_owner or owner_conflict" -v`
- `uv run pytest tests/coding_agent/test_pipeline_adapter.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -v`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
