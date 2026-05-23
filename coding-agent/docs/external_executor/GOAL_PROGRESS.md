# External Executor Goal Progress

This ledger tracks G127-G135 for the External Executor Adapter MVP phase.

## G127_EXTERNAL_EXECUTOR_CURRENT_STATE_MAP

### Before

- Goal id: G127_EXTERNAL_EXECUTOR_CURRENT_STATE_MAP
- Intended files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/external_executor/CURRENT_STATE.md`
- Verification commands:
  - `rg -n "ExternalExecutor|executor|BeeCommand|command bridge|workspace|validation|evidence|Prometheus|console" src/coding_agent tests/coding_agent tests/ui docs`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/external_executor/CURRENT_STATE.md`
  - `git diff --check -- .`
- Stop criteria:
  - Current Bee launch, Bee command bridge, workspace provider, validation runner, action safety, artifact, console, and observability surfaces are mapped.
  - Exact files/classes/functions to modify in later goals are identified.
  - Existing tests to preserve are identified.
  - Production Kubernetes, production Argo, Argo CD, nmem, homelab-specific logic, and multi-agent work are explicitly out of scope.
  - No production code changes are made.

### After

- Changed files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/external_executor/CURRENT_STATE.md`
- Tests run:
  - `rg -n "ExternalExecutor|executor|BeeCommand|command bridge|workspace|validation|evidence|Prometheus|console" src/coding_agent tests/coding_agent tests/ui docs`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/external_executor/CURRENT_STATE.md`
  - `git diff --check -- .`
- Results:
  - Mapped Bee launch/task state, Bee command bridge/local validation behavior, workspace provider boundaries, artifact/evidence handling, console, observability, and candidate files for later goals.
  - Confirmed no generic external executor model, registry, durable executor run store, Docker executor adapter, Kubernetes Job adapter, or Argo Workflow adapter exists yet.
  - Confirmed current command bridge executes only validation nodes through `ValidationRunner`; other command intent planning remains non-executing.
  - Confirmed external executors should be product-layer adapters that consume already-authorized execution plans and return sanitized result/evidence metadata.
- Remaining risks:
  - G128 still needs the ADR to lock down executor ownership, safety, storage, metrics, and adapter non-goals before production code changes.

## G128_EXTERNAL_EXECUTOR_ADR

### Before

- Goal id: G128_EXTERNAL_EXECUTOR_ADR
- Intended files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
- Verification commands:
  - `rg -n "ExternalExecutor|ExecutorKind|ExecutorCapability|ExecutorPlan|ExecutorRun|ExecutorResult|ExecutorEvidence|ExecutorLogSanitizer|ExecutorStatusImporter|Argo CD|Acceptance Criteria" docs/adr/0045-external-executor-adapter-boundaries.md`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md`
  - `git diff --check -- .`
- Stop criteria:
  - ADR defines external executor adapter ownership and model vocabulary.
  - ADR states external executors execute only already-authorized Bee execution plans.
  - ADR states external executors do not create tasks/topics/schedules/launches and do not decide node completion.
  - ADR preserves command policy, workspace policy, HITL, validation, no-leak, console, and observability contracts.
  - Docker/Kubernetes/Argo integrations are optional, disabled by default, and tested through fake/dry-run/capability paths.
  - Argo CD and homelab-specific integrations are out of scope.
  - No production code changes are made.

### After

- Changed files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
- Tests run:
  - `rg -n "ExternalExecutor|ExecutorKind|ExecutorCapability|ExecutorPlan|ExecutorRun|ExecutorResult|ExecutorEvidence|ExecutorLogSanitizer|ExecutorStatusImporter|Argo CD|Acceptance Criteria" docs/adr/0045-external-executor-adapter-boundaries.md`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md`
  - `git diff --check -- .`
- Results:
  - Added ADR-0045 as Accepted.
  - Defined executor kinds, capability reporting, executor plans, executor runs, executor results, executor evidence, log sanitization, status import, and registry boundaries.
  - Stated that external executors execute already-authorized Bee execution plans only and cannot create tasks/topics/schedules/launches or decide node completion.
  - Kept Docker/Kubernetes/Argo optional, disabled by default, and testable through fake/dry-run/capability paths.
- Remaining risks:
  - G129 still needs the concrete model/store/registry implementation and tests.
