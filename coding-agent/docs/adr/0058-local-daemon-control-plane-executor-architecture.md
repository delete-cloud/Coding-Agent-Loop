# ADR-0058: Local daemon control plane and executor architecture

**Status**: Accepted
**Date**: 2026-06-02

## Context

`coding_agent` has grown local CLI, remote HTTP, workspace, sandbox, checkpoint,
approval, and executor behavior from one runtime path. This made product
identity hard to reason about: clients can directly execute runtime work,
control-plane session management owns in-process runtime handles, workspace
placement is encoded through compatibility-oriented execution bindings, and
`coding_agent run` is easy to treat as a first-class product entrypoint even
though it cannot provide daemon durability, replay, or reconnect semantics.

The first version must stabilize the product boundary before adding cloud
managed execution or user-local attached executors. The durable path should be
run-centric: a client asks a control plane to start a run, the run coordinator
chooses an executor, and the executor owns runtime execution inside a sandboxed
environment over a workspace.

Existing AgentKit mechanisms remain valid. AgentKit owns the generic pipeline,
hook runtime, tape, plugin, tool, provider, directive, approval, and checkpoint
mechanisms. `coding_agent` owns product policy, session/run metadata, workspace
placement, daemon/server surfaces, executor placement, and sandbox policy.

## Decision

Adopt Local Daemon Mode as the first product architecture:

```text
Client
  -> ControlPlane / SessionService
  -> RunCoordinator
  -> Executor
  -> Runtime
  -> SandboxedEnvironment
  -> Workspace
```

The first product path is local daemon first:

```text
CLI / TUI / IDE Client
  -> Local Daemon ControlPlane
  -> LocalDaemonExecutor
  -> Runtime
  -> SandboxedEnvironment
  -> Local Workspace
```

The durable coordination layer manages session, run, approval, cancel, event,
checkpoint, resume, ownership, and policy metadata. It must not execute the
agent loop, run shell commands, directly read/write workspace contents, or hold
pipeline/runtime task handles as durable session data.

Clients are display and intent surfaces. They create sessions, start runs,
stream display events, submit approval decisions, cancel runs, and inspect
history/status. They do not own runtime execution.

Executors own runtime execution. A LocalDaemonExecutor is the first production
executor. ManagedPoolExecutor and local attached executor paths are future
extensions. InlineExecutor is testkit/devkit only and must still flow through
control-plane and run-coordinator abstractions when used for integration tests.

Introduce `RunTarget` as the canonical placement model:

```text
RunTarget = WorkspaceRef + ExecutorRef + IsolationPolicy + RunConstraints
```

`ExecutionBinding` becomes compatibility terminology. Existing APIs may
continue accepting and persisting it during migration, but new placement code
should use `RunTarget`. The migration must provide an `ExecutionBinding ->
RunTarget` adapter before replacing existing session metadata fields.

Sandbox is a wrapper policy, not an environment type. The intended relationship
is:

```text
Runtime -> SandboxedEnvironment -> Workspace
```

`coding_agent run` is no longer a target product path. It may remain as a
testkit/devkit/compatibility inline entrypoint while local daemon client
surfaces become the formal local product path.

Cloud Managed Mode is a future product path using the same architecture:

```text
Client -> Cloud ControlPlane -> ManagedPoolExecutor -> Runtime
  -> Provider Sandbox -> Cloud Workspace
```

This ADR does not implement that path.

## Alternatives Rejected

- Keep CLI single-process execution as a product path — rejected because it
  makes session lifecycle, replay, cancel, approval durability, background
  execution, and reconnect behavior ambiguous.
- Keep `SessionManager` as both durable control plane and runtime owner —
  rejected because it mixes session metadata, run coordination, event storage,
  approval state, workspace policy, pipeline runtime, and async task handles in
  one module.
- Continue evolving `ExecutionBinding` as the canonical model — rejected
  because it combines workspace surface and executor placement. `RunTarget`
  separates `WorkspaceRef`, `ExecutorRef`, `IsolationPolicy`, and
  `RunConstraints`.
- Implement Cloud Managed Mode and Local Daemon Mode together — rejected
  because the MVP should prove local durable run semantics before introducing
  managed executor pools, cloud workspaces, tenant policy, and Postgres
  coordination.
- Implement Cloud ControlPlane -> user LocalAttachedExecutor in the first
  version — rejected because that requires executor registration, claim
  protocol, heartbeat, fencing, event upload, approval/cancel relay, redaction,
  workspace privacy policy, and version negotiation.
- Remove `coding_agent run` immediately — rejected because existing tests,
  scripts, and dogfood workflows depend on a non-interactive entrypoint.
  Demotion should be explicit and staged.

## Acceptance Criteria

- [x] `RunTarget`, `WorkspaceRef`, `ExecutorRef`, `IsolationPolicy`, and
  `RunConstraints` exist under `src/coding_agent/runs/`.
- [x] `ExecutionBinding -> RunTarget` adapter maps local, cloud,
  external-worker, and local-attached compatibility bindings without changing
  existing serialized `ExecutionBinding` payloads.
- [x] New code in the run-placement skeleton imports canonical binding models
  from `coding_agent.environment.execution_binding`, not server/ui aliases.
- [x] `test_local_execution_binding_maps_to_local_daemon_run_target`
- [x] `test_cloud_execution_binding_maps_to_managed_pool_run_target`
- [x] `test_external_worker_binding_maps_to_external_worker_run_target`
- [x] `test_local_attached_binding_maps_to_local_attached_run_target`
- [x] `test_run_target_rejects_empty_annotations_key`
- [x] `uv run pytest tests/coding_agent/test_run_target.py tests/ui/test_execution_binding.py -v`
- [x] `uv run ruff check src/coding_agent/runs tests/coding_agent/test_run_target.py`

## Follow-up Implementation Tasks

- Split durable `SessionRecord` data from process-local `SessionRuntimeHandle`
  state.
- Introduce a `RunCoordinator` boundary that selects an executor from
  `RunTarget`.
- Move runtime ownership behind `LocalDaemonExecutor`.
- Demote `coding_agent run` to an inline testkit/devkit compatibility path.

## References

- `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0056-local-cli-control-plane-and-workspace-product-boundaries.md`
- `src/coding_agent/environment/execution_binding.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/cli/main.py`
- `src/coding_agent/cli/repl.py`
- `src/coding_agent/external_executor.py`
