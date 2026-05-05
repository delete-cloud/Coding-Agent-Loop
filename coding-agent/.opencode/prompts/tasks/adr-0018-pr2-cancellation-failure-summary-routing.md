Goal:
Harden ADR-0018 PR 2 so child subagent timeout, interruption, adapter failures, cleanup failures, and summary routing are deterministic at the parent surface.

Scope:
- Convert direct child adapter run exceptions into the existing subagent summary path instead of leaking raw adapter errors past the parent tool result.
- Keep `asyncio.CancelledError` as cancellation, not a swallowed child failure, while still releasing write leases and closing adapters.
- Make child interruption and adapter cleanup failures model-visible through deterministic subagent summaries.
- Preserve hidden child tape trace entries with `skip_context=True` and `subagent_child=True`.
- Preserve summary publication semantics: ordinary publisher failures are best-effort, but parent session ownership failures remain fail-fast.
- Ensure parent session ownership failures remain fail-fast even when subagent runs through generic tool execution or parallel batch execution.

Out of scope:
- Do not add PG-backed or provider-backed distributed child write leases.
- Do not run child agents in a separate process, queue worker, pod, or cloud callback handler.
- Do not add a real cloud vendor backend or credentials.
- Do not move subagent orchestration, cloud clients, owner routing, or approval policy into `agentkit`.
- Do not enable nested subagents.

Context:
- ADRs:
  - `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
  - `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
  - `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md`
  - `docs/adr/0017-cloud-workspace-execution.md`
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
- Relevant files:
  - `src/coding_agent/tools/subagent.py`
  - `src/coding_agent/subagents/coordinator.py`
  - `src/coding_agent/adapter.py`
  - `src/coding_agent/adapter_types.py`
  - `src/agentkit/tools/toolset.py`
  - `src/coding_agent/plugins/parallel_executor.py`
  - `src/coding_agent/ui/session_manager.py`
  - `tests/coding_agent/tools/test_subagent.py`
  - `tests/coding_agent/subagents/test_coordinator.py`
  - `tests/coding_agent/plugins/test_parallel_executor.py`
  - `tests/ui/test_session_manager_owner_checks.py`

Target tests:
- `uv run pytest tests/agentkit/tools/test_toolset.py -v`
- `uv run pytest tests/coding_agent/plugins/test_parallel_executor.py -v`
- `uv run pytest tests/coding_agent/tools/test_subagent.py tests/coding_agent/subagents/test_coordinator.py -v`
- `uv run pytest tests/coding_agent/test_bootstrap.py tests/ui/test_session_manager_public_api.py tests/ui/test_session_manager_owner_checks.py -k "subagent or cloud" -v`
- `uv run python -m compileall -q src tests/coding_agent tests/ui`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Stop and write a new ADR if implementation needs cross-process child execution, distributed leases, or real cloud vendor transport.
- Ignore non-blocking optimization suggestions.
