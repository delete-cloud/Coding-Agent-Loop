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

## Follow-up Implementation Status

- [x] Split durable `SessionRecord` data from process-local
  `SessionRuntimeHandle` state.
  - Durable session payloads are represented through `SessionRecord`.
  - Process-local runtime objects live under `SessionRuntimeHandle`.
  - Store payload tests assert runtime handles are not persisted.
- [x] Introduce a `RunCoordinator` boundary that selects an executor from
  `RunTarget`.
  - `DefaultRunCoordinator` preserves `RunTarget.executor`.
  - Local daemon runtime execution is delegated through
    `RunCoordinator.execute_runtime`.
  - Unsupported managed runtime execution is rejected by `RunCoordinator`, not
    prefiltered by `SessionManager`.
- [~] Move runtime ownership behind `LocalDaemonExecutor`.
  - Completed: local daemon runtime execution routes through
    `RunCoordinator.execute_runtime()` and `LocalDaemonExecutor`.
  - Completed: normal local runtime preparation is delegated from
    `SessionManager` to `LocalDaemonSessionRuntimeProvider`.
  - Completed: checkpoint restore runtime preparation still routes through
    `LocalDaemonExecutor.prepare_runtime()`.
  - Completed: local daemon turn completion finalization is delegated to
    `RuntimeTurnFinalizer` instead of living inside `run_agent`'s after-turn
    closure.
  - Completed: local daemon run start/finish guard state is tracked by
    `RuntimeTurnRunTracker` instead of `run_agent` nonlocal bookkeeping.
  - Completed: local daemon error handled/handler-failed guard state is tracked
    by `RuntimeTurnErrorState` instead of `run_agent` nonlocal bookkeeping.
  - Completed: local daemon before-turn runtime wiring is delegated to
    `RuntimeTurnStarter` instead of living inside `run_agent`'s before-turn
    closure.
  - Completed: local daemon fatal/cancelled/generic turn error actions are
    delegated to `RuntimeTurnErrorHandler` instead of living inside
    `run_agent`.
  - Completed: local daemon turn observation recorder state is tracked by
    `RuntimeTurnObservationState` instead of `run_agent` nonlocal closures.
  - Remaining: `SessionManager` still owns run lifecycle bookkeeping,
    checkpoint restore preparation details, wire consumer setup, and some
    runtime close/error policy. These should move behind narrower
    RunService/EventStore/Executor lifecycle boundaries before this item is
    considered complete.
- [x] Demote `coding_agent run` to an inline testkit/devkit compatibility path.
  - `run` records `origin.mode = inline_testkit`.
  - CLI and README describe `run` as dev/testkit one-shot compatibility.
  - Legacy `run --patch` and `run --verify-cmd` remain accepted but hidden and
    deprecated.
- [x] Make explicit `Session.default_run_target` placement authoritative while
  preserving `ExecutionBinding` compatibility.
  - Modern session payloads persist `default_run_target`.
  - Legacy payloads without `default_run_target` still derive placement from
    `execution_binding`.
  - Compatibility `execution_binding` assignment only updates derived targets;
    explicitly assigned or persisted targets remain authoritative.
- [~] Split internal `RuntimeEvent` facts from user-facing `DisplayEvent`
  projections.
  - Completed: `DisplayEvent` model and projection helpers exist under
    `coding_agent.events`.
  - Completed: `RuntimeEventReplayService` projects stored runtime events
    through a `RuntimeEventStore` service boundary and scans past internal-only
    runtime facts.
  - Completed: `SessionManager.replay_runtime_events()` and
    `SessionManager.replay_display_events()` are compatibility delegates to the
    replay service.
  - Completed: `GET /runs/{run_id}/display-events` exposes an additive
    user-facing replay endpoint without changing `GET /runs/{run_id}/events`.
  - Remaining: live SSE/UI rendering still uses existing wire/runtime event
    paths and should move to `DisplayEvent` projection separately.
- [~] Establish explicit Store contracts for durable runtime state.
  - Completed: `coding_agent.stores` defines runtime store contracts split into
    run lifecycle, run, runtime event, runtime interaction, and runtime
    checkpoint surfaces.
  - Completed: `SessionManager` depends on the shared `RuntimeStore` contract
    instead of an inline server-local protocol.
  - Completed: runtime event replay consumes the narrower `RuntimeEventStore`
    contract through `RuntimeEventReplayService`.
  - Remaining: run, approval, and checkpoint service ownership is still
    combined around a single runtime store dependency; those services should
    consume narrower contracts independently.

## Remaining Implementation Gaps

- Extract durable run lifecycle operations from `SessionManager` into a
  run/service boundary instead of inline `run_agent` closures.
- Make sandbox policy the default executor environment wrapper rather than a
  mixed local/cloud environment concern.
- Add daemon-backed client surfaces for the local product path. REPL/TUI/CLI
  clients should pair with or connect to the local daemon instead of owning an
  in-process runtime manager, and a daemon-backed non-interactive client should
  eventually replace one-shot inline `run` for product dogfood.

## References

- `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
- `docs/adr/0055-session-resume-and-interrupted-run-semantics.md`
- `docs/adr/0056-local-cli-control-plane-and-workspace-product-boundaries.md`
- `src/coding_agent/environment/execution_binding.py`
- `src/coding_agent/server/session_manager.py`
- `src/coding_agent/cli/main.py`
- `src/coding_agent/cli/repl.py`
- `src/coding_agent/external_executor.py`
