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
  - `src/coding_agent/bee_command_bridge.py`
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

## G130_LOCAL_EXECUTOR_ADAPTER_NORMALIZATION

### Before

- Goal id: G130_LOCAL_EXECUTOR_ADAPTER_NORMALIZATION
- Intended files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/bee_command_bridge.py src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/bee_command_bridge.py src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
- Stop criteria:
  - Existing Bee command bridge ready/allow plans can be normalized into local `ExecutorPlan` values without carrying raw command strings.
  - Local executor adapter records planned/running/final executor run status through `ExecutorRunStore`.
  - Local executor success and failure return sanitized `ExecutorResult`/`ExecutorEvidence`.
  - Denied, approval-required, wrong-kind, and missing-workspace plans cannot execute.
  - Bee node completion can consume executor evidence through the existing evidence gate.
  - Existing Bee command bridge and Bee launch tests still pass.

### After

- Changed files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
- Results:
  - Added `LocalExecutorAdapter` over already-approved local `ExecutorPlan` values.
  - Added `build_local_executor_plan_from_bee_command_plan` so only signed Bee command bridge `ready` plans with `allow` policy and `allow` approval route can become local executor plans.
  - Added executor result-to-Bee completion evidence conversion, preserving the existing evidence gate.
  - Local adapter records planned, running, and final sanitized result/evidence through the executor run store.
  - Denied, approval-required, wrong-kind, forged Bee plans, forged executor plans, mismatched workspace binding, missing workspace, and raw evidence refs reject before runner invocation or final durable result persistence.
  - Bee command bridge and Bee launch tests still pass.
- Remaining risks:
  - G131 still needs Docker adapter capability detection and dry-run rendering, disabled by default.

## G131_DOCKER_EXECUTOR_OPTIONAL_CAPABILITY

### Before

- Goal id: G131_DOCKER_EXECUTOR_OPTIONAL_CAPABILITY
- Intended files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
- Stop criteria:
  - Docker executor adapter is disabled by default.
  - Docker capability detection reports unavailable without a fake/real client.
  - Docker adapter can render a sanitized dry-run description from an authorized executor plan.
  - Denied or forged Docker plans are rejected.
  - No raw logs, command text, Docker names, env dumps, or secrets are stored.
  - No normal test requires Docker.

### After

- Changed files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
- Results:
  - Added `DockerExecutorAdapter` with disabled-by-default capability reporting.
  - Added fake-client capability checks for unavailable and available Docker states without requiring Docker.
  - Added dry-run rendering from signed Docker executor plans derived from authorized local plans.
  - Dry-run rendering hashes task/node/workspace references and excludes command text, stdout/stderr, secrets, and Docker runtime names.
  - Docker `submit` remains deferred and cannot execute containers in this goal.
  - Forged, unsigned, wrong-kind, or signature-mutated Docker plans are rejected.
- Remaining risks:
  - G132 still needs Kubernetes Job dry-run/fake-client rendering and status import.

## G132_KUBERNETES_JOB_EXECUTOR_DRY_RUN

### Before

- Goal id: G132_KUBERNETES_JOB_EXECUTOR_DRY_RUN
- Intended files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
- Stop criteria:
  - Kubernetes Job executor adapter is disabled by default.
  - Dry-run Job spec rendering works from signed Kubernetes executor plans.
  - Fake status import maps running, succeeded, and failed to executor statuses.
  - Sanitized evidence is generated without pod/job names, kubeconfig, env dumps, raw logs, or command text.
  - No normal test requires Kubernetes, kubectl, or a cluster.

### After

- Changed files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
- Results:
  - Added `KubernetesJobExecutorAdapter` with disabled-by-default capability reporting.
  - Added signed Kubernetes Job executor plan derivation from authorized local executor plans.
  - Added sanitized dry-run Job spec rendering without raw command text, pod/job names, kubeconfig, env dumps, or secrets.
  - Added fake status import for running, succeeded, and failed states with sanitized evidence metadata.
  - Kubernetes `submit` remains deferred and no normal test requires Kubernetes, kubectl, or a cluster.
- Remaining risks:
  - G133 still needs Argo Workflow dry-run/fake-client rendering and status import.

## G133_ARGO_WORKFLOW_EXECUTOR_DRY_RUN

### Before

- Goal id: G133_ARGO_WORKFLOW_EXECUTOR_DRY_RUN
- Intended files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Verification commands:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
- Stop criteria:
  - Argo Workflow executor adapter is disabled by default.
  - Dry-run Workflow spec rendering works from signed Argo executor plans.
  - Fake workflow status import maps running, succeeded, and failed to executor statuses.
  - Sanitized evidence is generated without workflow names, pod names, kubeconfig, env dumps, raw logs, Argo CD integration, or command text.
  - No normal test requires Argo Workflows, Argo CLI, Kubernetes, or a cluster.

### After

- Changed files:
  - `docs/external_executor/GOAL_PROGRESS.md`
  - `docs/adr/0045-external-executor-adapter-boundaries.md`
  - `src/coding_agent/external_executor.py`
  - `tests/coding_agent/test_external_executor.py`
- Tests run:
  - `uv run pytest tests/coding_agent/test_external_executor.py -v`
  - `uv run pytest tests/coding_agent/test_bee_command_bridge.py -q`
  - `uv run pytest tests/coding_agent/test_bee_launch.py -q`
  - `uv run ruff format --check --preview docs/external_executor/GOAL_PROGRESS.md docs/adr/0045-external-executor-adapter-boundaries.md src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `uv run ruff check src/coding_agent/external_executor.py tests/coding_agent/test_external_executor.py`
  - `git diff --check -- .`
- Results:
  - Added `ArgoWorkflowExecutorAdapter` with disabled-by-default capability reporting.
  - Added signed Argo Workflow executor plan derivation from authorized local executor plans.
  - Added sanitized dry-run Workflow spec rendering without raw command text, workflow names, pod names, kubeconfig, env dumps, Argo CD references, or secrets.
  - Added fake status import for running, succeeded, failed, and errored phases with sanitized evidence metadata.
  - Argo `submit` remains deferred and no normal test requires Argo Workflows, Argo CLI, Kubernetes, or a cluster.
- Remaining risks:
  - G134 still needs console, task artifact, report/evidence, metrics, and trace integration for executor runs.
