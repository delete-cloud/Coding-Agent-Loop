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
