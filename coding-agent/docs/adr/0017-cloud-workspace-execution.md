# ADR-0017: Add cloud workspace execution as an environment-backed vertical slice

**Status**: Proposed
**Date**: 2026-05-04

## Context

ADR-0014 separated durable session execution binding from runtime ownership by
adding `LocalExecutionBinding` and `CloudWorkspaceBinding`. ADR-0016 then staged
the prerequisites for remote execution: `agentkit.environment.Environment`,
`coding_agent.environment.LocalEnvironment`, `AgentRunContext`, `Toolset`,
`RuntimeMessageBus`, and dynamic tool proxy governance. Those pieces are now in
place, but `CloudWorkspaceBinding` still fails explicitly through
`DefaultBindingResolver`, and several app-layer paths still assume an
environment can always produce a local `workspace_root`.

The next step is not full cloud workspace orchestration. The next step is a
minimal, testable cloud execution vertical slice that proves the ADR-0016
environment boundary works for file, patch, grep/glob, and shell tools without
teaching `agentkit` about any concrete cloud provider. Full subagent
orchestration remains a follow-up because child runs already inherit the parent
`Environment`; implementing child orchestration before cloud execution would
spread local path and in-process lease assumptions into the orchestration layer.

Remote workspace systems expose the same recurring surface: workspace identity,
file read/write operations, command execution with cwd/env/timeout, lifecycle
operations such as pause/resume/close, and deterministic error reporting. For
example, OpenHands documents `LocalWorkspace` and `RemoteWorkspace` behind a
shared workspace API with `execute_command`, file transfer, health, pause, and
resume operations. OpenComputer and Daytona expose sandbox APIs with filesystem
methods and shell/process execution accepting cwd, env, and timeout. ADR-0017
adopts those shape constraints while keeping this repository provider-neutral.

## Decision

Implement cloud workspace execution before full subagent orchestration, in four
small PRs.

1. PR 1: De-localize the environment boundary
   - Extend `agentkit.environment.Environment` with model-facing workspace
     summary data that does not require a local `Path`.
   - Keep `LocalEnvironment.workspace_root` and local `workspace_root` config for
     existing CLI, skills, shell session, and local sandbox behavior.
   - Stop requiring every injected environment to return
     `tool_config()["workspace_root"]` inside `coding_agent.app.create_agent`,
     `create_child_pipeline`, and `CoreToolsPlugin`.
   - Keep `workspace_root` as optional compatibility metadata in
     `PipelineContext.config` only when the environment supplies a local root.
   - Keep `ShellSessionPlugin` local-root aware, but let non-local environments
     initialize cwd from the environment summary/default cwd instead of
     `Path.cwd()` when available.

2. PR 2: Add a provider-neutral `CloudEnvironment` with fake cloud client tests
   - Add `coding_agent.environment.cloud` with a small
     `CloudWorkspaceClient` protocol owned by `coding_agent`, not `agentkit`.
   - The client surface must cover workspace identity, file read/write/replace,
     glob, grep, patch, and shell command execution with cwd/env/timeout.
   - Add an in-memory fake client for tests. Do not add a real vendor dependency
     in this PR.
   - Implement `CloudEnvironment` by returning tool callables that delegate to
     the client and preserve existing tool names: `file_read`, `file_write`,
     `file_replace`, `glob_files`, `grep_search`, `file_patch`, and `bash_run`.
   - Cloud tool failures must return tool errors through the existing Toolset
     envelope path; they must not silently fall back to local execution.

3. PR 3: Resolve cloud bindings into cloud environments in HTTP sessions
   - Extend `DefaultBindingResolver` or inject a resolver factory so
     `CloudWorkspaceBinding` can resolve to `CloudEnvironment` when cloud client
     configuration is available.
   - Keep unresolved cloud bindings fail-fast with a typed error. Do not partially
     execute cloud sessions through local `workspace_root` assumptions.
   - Add HTTP/session creation support for storing cloud execution bindings.
   - Ensure `run_agent`, `ensure_session_runtime`, checkpoint restore, and child
     pipeline creation receive the resolved cloud environment.
   - Owner leases and fencing remain in the HTTP/session layer from ADR-0013 and
     ADR-0015; cloud execution must not bypass those owner checks.

4. PR 4: Harden cloud execution lifecycle and parity
   - Add cwd/env persistence semantics for cloud shell execution that match the
     existing local shell session behavior where possible.
   - Add deterministic timeout, cancellation, and error propagation contracts for
     cloud shell and file operations.
   - Add trace metadata and prompt-time runtime context that identify the cloud
     workspace without exposing secrets.
   - Add local/cloud parity tests for the common tool contract.
   - Add failure-path tests proving cloud tool errors are visible to the model and
     do not mutate local files.

The ownership boundary remains the one established by ADR-0016:

- `agentkit.environment`: generic environment protocol and shared callable/result
  types only.
- `agentkit.runtime`: run identity, prompt-time runtime context, and message
  consumption semantics only.
- `agentkit.tools`: generic tool governance only.
- `coding_agent`: `CloudWorkspaceBinding`, `CloudEnvironment`, cloud client
  configuration, HTTP/session binding resolution, local/cloud shell behavior,
  provider credentials, product-specific risk policy, and tests.

Full subagent orchestration starts after PR 1 through PR 4. It may rely on the
fact that parent and child runs inherit the same `Environment`, but it must not
move provider-specific cloud clients into `agentkit`. Cross-process child write
coordination requires its own ADR because the current `ChildWorkerCoordinator`
uses an in-process `asyncio.Lock`.

## Alternatives Rejected

- Implement full subagent orchestration first — rejected because the current
  subagent tool inherits parent `Environment`; without a cloud environment, full
  orchestration would codify local path and in-process write-lock assumptions.
- Treat cloud workspaces as fake local `workspace_root` paths — rejected because
  a remote workspace has different identity, cwd, timeout, cancellation, and
  credential semantics from a local `Path`.
- Put `CloudWorkspaceClient` in `agentkit` — rejected because concrete cloud
  provider clients, credentials, HTTP APIs, and product policy are
  `coding_agent` integration concerns.
- Implement a real vendor backend in the first PR — rejected because it would
  mix interface validation, provider credentials, remote transport, and app
  wiring before the local/cloud contract is executable in tests.
- Route cloud tools only through `search_tools`/`call_tool` — rejected for the
  first cut because core file, shell, patch, glob, and grep tools remain the
  clearest model-facing interface for common coding workflows.
- Add brokered event routing or resumable in-flight cloud turns now — rejected
  because ADR-0013 intentionally keeps first-cut failover at at-most-once
  execution, and ADR-0015 keeps event routing sticky-owner only.

## Acceptance Criteria

- [ ] `test_environment_workspace_summary_does_not_require_local_workspace_root`
- [ ] `test_create_agent_accepts_non_local_environment_without_workspace_root`
- [ ] `test_core_tools_accepts_non_local_environment_without_workspace_root`
- [ ] `test_cloud_environment_file_tools_use_client_without_local_filesystem`
- [ ] `test_cloud_environment_shell_tool_preserves_cwd_and_env`
- [ ] `test_cloud_environment_tool_errors_do_not_fallback_to_local_execution`
- [ ] `test_cloud_binding_resolves_to_cloud_environment_when_client_available`
- [ ] `test_cloud_binding_without_client_fails_fast_with_typed_error`
- [ ] `test_run_agent_passes_cloud_environment_from_execution_binding`
- [ ] `test_restore_checkpoint_preserves_cloud_execution_binding`
- [ ] `test_http_create_session_stores_cloud_execution_binding`
- [ ] `test_cloud_owner_change_rejects_stale_owner_tool_execution`
- [ ] `uv run pytest tests/agentkit/environment/ tests/coding_agent/environment/ tests/coding_agent/plugins/test_core_tools.py tests/coding_agent/test_bootstrap.py tests/ui/test_execution_binding.py tests/ui/test_session_manager_runtime.py tests/ui/test_http_server.py -k "environment or cloud or binding or workspace" -v`

## References

- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0014-separate-session-execution-binding-from-runtime-ownership.md`
- `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
- `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md`
- `src/agentkit/environment/protocols.py`
- `src/agentkit/runtime/pipeline.py`
- `src/coding_agent/app.py`
- `src/coding_agent/environment/local.py`
- `src/coding_agent/plugins/core_tools.py`
- `src/coding_agent/plugins/shell_session.py`
- `src/coding_agent/ui/binding_resolver.py`
- `src/coding_agent/ui/execution_binding.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/tools/subagent.py`
- `https://docs.openhands.dev/sdk/api-reference/openhands.sdk.workspace`
- `https://docs.opencomputer.dev/reference/python-sdk`
- `https://daytona.io/docs/en/process-code-execution/`
