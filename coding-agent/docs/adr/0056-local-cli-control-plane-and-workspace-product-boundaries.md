# ADR-0056: Local CLI, Control Plane, and Workspace Product Boundaries

**Status**: Proposed
**Date**: 2026-06-01

## Context

`coding_agent` now contains three product shapes that grew from the same
runtime: a local interactive CLI, a remote HTTP control plane, and an executor
plane for sandboxed or attached execution. The current implementation shares
large session/runtime objects across those shapes. For example, the local REPL
uses the server `SessionManager`, and the server session model stores both
durable metadata and in-process runtime handles.

This makes the product meaning of `local`, `remote`, `session`, `workspace`,
and `executor` unclear. A local CLI session should behave like a Codex or Claude
Code session: one user, one local workspace, an interactive REPL, local approval
prompts, tape persistence, model switching, and checkpoint restore. The remote
server should be a control plane for sessions, runs, workspaces, approvals, and
events. Executors should be an execution plane that consumes authorized plans
and reports status and results.

The existing runtime direction remains valid: AgentKit owns the generic
Pipeline, Hook Runtime, Tape, Plugin, Tool, Provider, Directive, Approval, and
Checkpoint mechanisms. Coding Agent owns product policy and workspace semantics.

## Decision

Separate product meanings before moving large amounts of code.

The local CLI product must depend on a local runtime/session abstraction rather
than on the HTTP control-plane `SessionManager`. That abstraction may reuse
`app.create_agent`, `PipelineAdapter`, tape stores, checkpoint stores, and
approval components, but it should not expose remote session, workspace
inventory, owner lease, executor claim, or HTTP event-queue concerns to the REPL.

The server/control-plane session service remains responsible for HTTP sessions,
runs, workspace lifecycle, approval interactions, event streaming, resume
metadata, and executor placement. It should evolve toward a split between
durable records and in-process runtime handles.

Workspace is the execution boundary for file, patch, shell, archive, diff,
publish, cleanup, and validation operations. Sessions should record their
workspace through `ExecutionBinding`/workspace metadata. Local repositories,
Docker sandbox workspaces, future OS-native sandboxes, cloud workspaces, and
external executor workspace references should be represented as explicit
workspace bindings or provider-backed environments instead of being inferred
from unrelated session fields.

Dogfood this refactor in two bounded tasks:

1. Local CLI runtime boundary: introduce or prepare a local CLI runtime/session
   abstraction and remove the local REPL's direct dependency on the server
   `SessionManager` in the smallest behavior-preserving slice.
2. Workspace/sandbox/control-plane boundary: clarify workspace binding/provider
   terminology and prepare executor-related code to live behind an execution
   plane boundary without changing existing remote protocols.

## Alternatives Rejected

- Keep sharing `server.SessionManager` with local CLI — rejected because it
  makes the simplest local product path depend on remote/control-plane and
  executor concepts.
- Move all session, workspace, remote, and executor files in one refactor —
  rejected because it would create high-risk import churn before the product
  terms are stable.
- Put workspace provider semantics into AgentKit — rejected because repository
  paths, Docker, cloud clients, sandbox policy, and workspace lifecycle are
  Coding Agent product concerns.
- Treat Docker sandbox as the only workspace abstraction — rejected because the
  intended model also includes local repositories, future OS-native sandboxing,
  cloud workspaces, and external executor workspace references.

## Acceptance Criteria

- [ ] Local CLI task packet exists and names target tests for the REPL/session boundary.
- [ ] Workspace/sandbox task packet exists and names target tests for workspace binding/provider semantics.
- [ ] `test_repl_does_not_import_server_session_manager_for_local_runtime`
- [ ] `test_local_cli_runtime_preserves_model_switch_and_checkpoint_restore`
- [ ] `test_workspace_binding_product_terms_round_trip`
- [ ] `uv run pytest tests/cli/test_repl.py tests/cli/test_commands.py -k "managed_session or model or checkpoint or local_runtime" -v`
- [ ] `uv run pytest tests/ui/test_execution_binding.py tests/coding_agent/environment/test_local_environment.py tests/coding_agent/environment/test_docker_workspace_provider.py -k "binding or workspace_provider or unavailable" -v`

## References

- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- `docs/adr/0048-application-structure-refactor-boundaries.md`
- `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `src/coding_agent/cli/repl.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/server/execution_binding.py`
- `src/coding_agent/environment/`
- `src/coding_agent/external_executor.py`
