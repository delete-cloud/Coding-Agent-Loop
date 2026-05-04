Goal:
Codify ADR-0018 PR 1 by adding focused local/cloud parity regressions for owner-local subagent orchestration, without introducing distributed child-worker coordination.

Scope:
- Strengthen `coding_agent.tools.subagent` tests so child runs inherit the parent `Environment`, including a cloud `CloudEnvironment` backed by a fake client.
- Add regressions proving reserved `subagent.*` metadata is authoritative while ADR-0017 cloud metadata remains authoritative and secret-free.
- Keep nested subagent delegation unavailable from child toolsets and verify the child prompt/tool-filter contract still holds.
- Keep mutating child tool deltas serialized by the owner-local `ChildWorkerCoordinator` write lease.
- Verify child summary publication still routes through the parent session runtime message bus and respects owner checks.

Out of scope:
- Do not add PG-backed or provider-backed distributed child write leases.
- Do not run child agents in a separate process, queue worker, pod, or cloud callback handler.
- Do not add a real cloud vendor backend or credentials.
- Do not move `CloudWorkspaceClient`, `CloudEnvironment`, `ChildWorkerCoordinator`, HTTP owner routing, or approval policy into `agentkit`.
- Do not enable nested subagents.

Context:
- ADRs:
  - `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
  - `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
  - `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md`
  - `docs/adr/0017-cloud-workspace-execution.md`
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
- Relevant files:
  - `src/agentkit/runtime/context.py`
  - `src/coding_agent/app.py`
  - `src/coding_agent/tools/subagent.py`
  - `src/coding_agent/subagents/coordinator.py`
  - `src/coding_agent/ui/session_manager.py`
  - `tests/coding_agent/tools/test_subagent.py`
  - `tests/coding_agent/subagents/test_coordinator.py`
  - `tests/coding_agent/environment/test_cloud_environment.py`
  - `tests/coding_agent/test_bootstrap.py`
  - `tests/ui/test_session_manager_public_api.py`

Target tests:
- `uv run pytest tests/coding_agent/tools/test_subagent.py tests/coding_agent/subagents/test_coordinator.py -v`
- `uv run pytest tests/coding_agent/test_bootstrap.py tests/ui/test_session_manager_public_api.py -k "subagent or cloud" -v`

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
