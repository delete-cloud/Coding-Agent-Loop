# ADR-0018: Keep cloud-aware subagent orchestration owner-local

**Status**: Proposed
**Date**: 2026-05-04

## Context

ADR-0016 staged the runtime pieces that subagents need before they can become a
first-class orchestration surface: `AgentRunContext`, environment sharing,
tool-governed execution, runtime messages, and prompt-time runtime context.
ADR-0017 then added provider-neutral cloud workspace execution and deliberately
deferred full subagent orchestration until the local/cloud tool contract was
executable.

The current subagent implementation already has useful foundations:

- `coding_agent.tools.subagent.build_subagent_tool` forks a child tape, derives a
  child run context from the parent `AgentRunContext`, forwards the parent
  `Environment` to `create_child_pipeline`, disables nested subagents through a
  `tool_filter`, appends hidden child trace entries back to the parent tape, and
  publishes child summaries through the parent session runtime message bus.
- `coding_agent.subagents.ChildWorkerCoordinator` allocates child agent ids and
  serializes mutating child tool events with an in-process `asyncio.Lock`.
- ADR-0013 and ADR-0015 already define HTTP execution as owner-routed and
  at-most-once: the parent session owner is the only process allowed to execute
  runtime-sensitive work for that session.

The open design question is whether subagent write coordination should become a
cross-process PostgreSQL-backed lease now that cloud workspaces exist. It should
not. Cloud workspaces change where tools execute; they do not, by themselves,
make child agents independently schedulable workers. Until a child can run
outside the parent session owner process, a PG-backed child lease would add
coordination complexity without protecting a real cross-process actor.

## Decision

Implement full subagent orchestration as **owner-local, cloud-aware execution**.

The core invariant is:

> A child agent is an execution-local implementation detail of the parent turn.
> Only the current parent session owner may start, run, cancel, emit tool deltas
> for, and publish summaries from child agents.

This means:

- Child runs must inherit the parent `Environment`. For a cloud-bound parent,
  the child must use the same `CloudEnvironment` and therefore the same
  provider-neutral `CloudWorkspaceClient` instance supplied by `coding_agent`.
- Child runs must derive `AgentRunContext` from the parent run: same session id,
  new child run id, child agent id, parent run id, inherited context budget, and
  merged trace metadata.
- Reserved `subagent.*` trace metadata is owned by the subagent dispatcher and
  must overwrite caller-supplied spoofed values.
- Cloud trace metadata remains governed by ADR-0017: caller-supplied `cloud.*`
  keys are stripped and the environment injects only authoritative
  `cloud.workspace_id`.
- Nested subagent delegation remains disabled for child toolsets. This is a
  safety property, not just a prompt optimization.
- Child summaries continue to publish through the parent session runtime message
  bus, under the parent owner/fencing checks from ADR-0013 and ADR-0015.
- Mutating child tool events continue to acquire the owner-local
  `ChildWorkerCoordinator` write lease. The lease serializes child writes within
  one owner process; it is not a distributed lock.
- Provider-specific cloud clients, credentials, and workspace lifecycle remain in
  `coding_agent`; no concrete cloud client moves into `agentkit`.

Cross-process child orchestration is explicitly deferred. Add a new ADR before
allowing any of these behaviors:

- scheduling child agents in a separate process, pod, queue worker, or cloud
  callback handler;
- receiving child tool deltas from a process that is not the parent session
  owner;
- sharing one cloud workspace across independently owned parent/child runtimes;
- replacing the owner-local child write lease with a PostgreSQL-backed or
  provider-backed distributed lease.

## Implementation Plan

### PR 1: Codify owner-local local/cloud parity

Goal: make the current owner-local contract executable and cloud-aware without
adding a new orchestration layer.

Affected paths:

- `src/coding_agent/tools/subagent.py`
- `src/coding_agent/subagents/coordinator.py`
- `src/coding_agent/app.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/coding_agent/tools/test_subagent.py`
- `tests/coding_agent/subagents/test_coordinator.py`
- `tests/coding_agent/environment/test_cloud_environment.py`
- `tests/coding_agent/test_bootstrap.py`
- `tests/ui/test_session_manager_public_api.py`

Implementation notes:

- Prefer strengthening existing tests before adding new production types.
- Reuse the fake cloud workspace client pattern from
  `tests/coding_agent/environment/test_cloud_environment.py` rather than adding a
  real provider dependency.
- Add only the minimum helper code needed to make the subagent contract explicit.
- Keep `tool_filter=lambda tool_name: tool_name != "subagent"` as the nested
  delegation guard unless a later ADR replaces it with a broader policy object.
- Keep `ChildWorkerCoordinator` in `coding_agent`; do not introduce a PG-backed
  child lease in this PR.

### PR 2: Harden cancellation, failure, and summary routing

Goal: make child failures deterministic at the parent surface.

Scope:

- Child timeout, cancellation, and adapter close behavior remains model-visible
  through the existing subagent summary path.
- Summary publication remains best-effort for child result return, but ownership
  failures from the parent session manager remain fail-fast.
- Hidden child tape entries remain marked `skip_context=True` and
  `subagent_child=True` so context extraction does not treat them as parent user
  turns.

### Future ADR: Distributed child workers

Write a new ADR before this repository allows child agents to run outside the
parent session owner. That ADR must define the durable coordination mechanism,
fencing semantics, retry/idempotency model, and cloud workspace write policy.

## Alternatives Rejected

- Add PG-backed child write leases now — rejected because child agents are still
  executed only inside the parent session owner process. A distributed lease would
  not protect any current cross-process actor and would add a second coordination
  model beside ADR-0013 ownership.
- Move subagent orchestration into `agentkit` now — rejected because the current
  orchestration policy depends on `coding_agent` concerns: concrete
  environments, provider configuration, HTTP owner routing, approval behavior,
  child summary publication, and product-specific tool risk policy.
- Enable nested subagents for child runs — rejected because it complicates write
  coordination, trace attribution, and parent summary semantics before the first
  owner-local cloud-aware contract is tested.
- Implement a real cloud vendor child-worker backend now — rejected because
  network transport, credentials, sandbox lifecycle, and distributed child
  scheduling are separate concerns from proving the owner-local contract.
- Treat at-most-once HTTP execution as sufficient by itself — rejected as an
  incomplete argument. The safety condition is stronger: no non-owner process may
  execute a child or emit child tool deltas for the session.

## Acceptance Criteria

- [x] `test_subagent_tool_forwards_parent_environment_to_child_builder`
- [x] `test_subagent_tool_forwards_cloud_environment_to_child_builder`
- [x] `test_subagent_tool_preserves_authoritative_cloud_trace_metadata`
- [x] `test_subagent_tool_overwrites_caller_supplied_reserved_trace_keys`
- [x] `test_subagent_tool_child_system_prompt_explicitly_disables_nested_subagent`
- [x] `test_subagent_tool_appends_hidden_child_trace_to_parent_tape`
- [x] `test_subagent_tool_acquires_write_lease_for_mutating_child_tool_event`
- [x] `test_subagent_tool_skips_write_lease_for_read_only_child_turn`
- [x] `test_subagent_tool_publishes_completion_summary_to_parent_session`
- [x] `test_subagent_summary_publish_rejects_stale_owner`
- [x] `uv run pytest tests/coding_agent/tools/test_subagent.py tests/coding_agent/subagents/test_coordinator.py tests/coding_agent/test_bootstrap.py tests/ui/test_session_manager_public_api.py -k "subagent or cloud" -v`

## References

- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
- `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md`
- `docs/adr/0017-cloud-workspace-execution.md`
- `src/agentkit/runtime/context.py`
- `src/coding_agent/app.py`
- `src/coding_agent/tools/subagent.py`
- `src/coding_agent/subagents/coordinator.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/coding_agent/tools/test_subagent.py`
- `tests/coding_agent/subagents/test_coordinator.py`
