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

## G129_EXECUTOR_MODEL_STORE_AND_REGISTRY

### Before

- Goal id: G129_EXECUTOR_MODEL_STORE_AND_REGISTRY
- Intended files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
- Stop criteria:
  - Generic external executor model, protocol, registry, and durable executor run store exist in the Coding Agent product layer.
  - Store schema initialization is idempotent.
  - Store APIs cover create, load, status update, sanitized result/evidence attachment, and list by task/node.
  - Registry tests cover known and unknown executor kinds.
  - Existing Bee launch and Bee command bridge behavior still passes.
  - No executor performs real local, Docker, Kubernetes, or Argo execution in this goal.

### After

- Changed files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
- Results:
  - Added the generic `ExternalExecutor` protocol, executor capability/plan/result/evidence models, executor registry, and `PGExecutorRunStore`.
  - Store schema is idempotent and limited to `executor_runs`; it does not migrate or couple to Bee task tables.
  - Store tests cover create, load, duplicate create rejection, status update, sanitized result/evidence attachment, list filtering by task/node/kind/status, missing-row failure, and sensitive metadata/reference rejection.
  - Bee launch and Bee command bridge tests still pass.
- Remaining risks:
  - G130 still needs to normalize the existing local safe execution path behind this interface and prove denied or approval-required plans cannot execute.
