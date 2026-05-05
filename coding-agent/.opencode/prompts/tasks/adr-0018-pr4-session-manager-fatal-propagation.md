Goal:
Preserve ADR-0018 fatal tool execution failures end-to-end through the pipeline, subagent, session manager, and HTTP prompt surface so generic fatal subclasses keep the same fail-fast SSE error behavior as stale-owner conflicts.

Scope:
- Re-raise `FatalToolExecutionError` from `Pipeline.run_turn()` stage wrappers instead of downgrading it into `PipelineError`.
- Re-raise generic `FatalToolExecutionError` from subagent summary publication and child `run_turn()` paths instead of only `SessionOwnershipConflictError`.
- Re-raise `FatalToolExecutionError` from `SessionManager.run_agent()` while still closing runtime state.
- Keep `/sessions/{session_id}/prompt` behavior consistent with the streaming contract: mid-turn fatal tool errors and fatal subagent summary publication failures surface as SSE `Error` events instead of fake assistant `StreamDelta` + `TurnEnd(ERROR)` output.

Out of scope:
- Do not change ADR-0018 document status in this PR.
- Do not change pre-turn owner authorization or event-stream ownership behavior.
- Do not add new fatal subclasses, distributed child-worker coordination, or cloud callback execution.
- Do not refactor unrelated pipeline, subagent, HTTP, or session-manager exception handling.

Context:
- ADRs:
  - `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
  - `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
- Relevant files:
  - `src/agentkit/runtime/pipeline.py`
  - `src/coding_agent/tools/subagent.py`
  - `src/coding_agent/ui/session_manager.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/agentkit/runtime/test_pipeline.py`
  - `tests/coding_agent/tools/test_subagent.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/ui/test_http_server.py`

Target tests:
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "fatal_tool_execution_error_from_batch_hook or batch_results_are_too" -v`
- `uv run pytest tests/coding_agent/tools/test_subagent.py -k "fatal_summary_publish or fatal_tool_execution_error_from_child_run_turn or stale_owner" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "fatal_tool_execution or owner_conflict_without_sending_error_turn" -v`
- `uv run pytest tests/ui/test_http_server.py -k "fatal_subagent_summary_publish or fatal_tool_execution or owner_conflict" -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py tests/coding_agent/tools/test_subagent.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py tests/ui/test_http_server_failover.py tests/ui/test_session_manager_public_api.py -v`
- `uv run python -m compileall -q src tests/agentkit/runtime tests/coding_agent/tools tests/ui`

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
