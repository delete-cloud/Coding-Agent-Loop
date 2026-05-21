# Bee Runtime Goal Progress

This ledger tracks G93-G101 for the Bee-style Workflow Template / Task Manifest Runtime phase.

## G93_BEE_RUNTIME_CURRENT_STATE_MAP

### Before

- Goal id: G93_BEE_RUNTIME_CURRENT_STATE_MAP
- Intended files:
  - `docs/bee_runtime/GOAL_PROGRESS.md`
  - `docs/bee_runtime/CURRENT_STATE.md`
- Verification commands:
  - `test -f docs/bee_runtime/GOAL_PROGRESS.md`
  - `test -f docs/bee_runtime/CURRENT_STATE.md`
  - `rg -n "Bee|Task|Topic|Schedule|Workspace|Action Safety|Observability|No production credentials" docs/bee_runtime`
  - `git diff --check -- .`
- Stop criteria:
  - Current Topic, Schedule, Workspace, Action Safety, Context, Observability, and Developer Console extension points are mapped.
  - Existing Bee/task runtime gaps are identified without production code changes.
  - Future implementation files and tests are named precisely enough for G94-G101.
  - No homelab-specific, external executor, Kubernetes, Argo, desktop, bridge, or multi-agent behavior is introduced.

### After

- Status: merged via PR #303.
- Changed files:
  - `docs/bee_runtime/GOAL_PROGRESS.md`
  - `docs/bee_runtime/CURRENT_STATE.md`
- Tests run:
  - `test -f docs/bee_runtime/GOAL_PROGRESS.md`
  - `test -f docs/bee_runtime/CURRENT_STATE.md`
  - `rg -n "Bee|Task|Topic|Schedule|Workspace|Action Safety|Observability|No production credentials" docs/bee_runtime`
  - `git diff --check -- .`
- Results:
  - Bee runtime current-state document exists and maps the relevant extension points.
  - Goal ledger exists and records G93 before/after state.
  - required keyword scan passed.
  - whitespace diff check passed.
- Remaining risks:
  - G93 is documentation-only. ADR, manifest parsing, durable task store, topic anchors, launch planning, console/observability integration, and final smoke coverage are deferred to G94-G101.
  - Local review found that the first draft omitted the legacy `TopicPlugin`; G93 now documents its `topic_start`/`topic_end` anchor behavior and raw-message compatibility risk for G97.

## G94_BEE_RUNTIME_ADR

### Before

- Goal id: G94_BEE_RUNTIME_ADR
- Intended files:
  - `docs/bee_runtime/GOAL_PROGRESS.md`
  - `docs/adr/0041-bee-workflow-task-runtime.md`
- Verification commands:
  - `test -f docs/adr/0041-bee-workflow-task-runtime.md`
  - `rg -n "Bee|Topic|TaskManifest|Action Safety|Prometheus|homelab|Acceptance Criteria" docs/adr/0041-bee-workflow-task-runtime.md`
  - `git diff --check -- .`
- Stop criteria:
  - ADR defines Bee as a generic Coding Agent task/workflow profile built on Topic.
  - ADR keeps AgentKit Core generic and prevents a parallel durable-runtime or action-execution path.
  - ADR excludes homelab-specific templates, external executors, Kubernetes, Argo, desktop, bridge, and multi-agent task graphs.
  - ADR includes executable G95-G101 acceptance criteria.

### After

- Status: passed local verification; pending PR.
- Changed files:
  - `docs/bee_runtime/GOAL_PROGRESS.md`
  - `docs/adr/0041-bee-workflow-task-runtime.md`
- Tests run:
  - `test -f docs/adr/0041-bee-workflow-task-runtime.md`
  - `rg -n "Bee|Topic|TaskManifest|Action Safety|Prometheus|homelab|Acceptance Criteria" docs/adr/0041-bee-workflow-task-runtime.md`
  - `git diff --check -- .`
- Results:
  - ADR-0041 exists and is Accepted.
  - ADR captures Bee as a generic Coding Agent task/workflow profile over Topic.
  - ADR rejects homelab-specific templates, external executors, Kubernetes, Argo, desktop, bridge, and multi-agent task graphs for this phase.
  - whitespace diff check passed.
- Remaining risks:
  - G94 is ADR-only. Manifest parser, durable store, topic anchors, planning, console/observability integration, and final smoke coverage are deferred to G95-G101.
