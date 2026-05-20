# Workspace Provider / Sandbox MVP Goal Progress

Date started: 2026-05-21

## Phase Scope

This phase makes workspace providers explicit and completes a sandbox MVP over
the existing Coding Agent workspace execution surfaces. It must keep AgentKit
Core generic, avoid rewriting the pipeline, preserve G00-G67 behavior, and avoid
requiring production credentials, hosted services, or Docker for all tests.

## Planned Goals

| Goal | Scope | Expected result |
| --- | --- | --- |
| G68 | Current-state map. | Document existing workspace provider, execution binding, sandbox, API, console, and test surfaces. |
| G69 | Boundary ADR. | Define provider/sandbox MVP boundaries, non-goals, privacy rules, metrics rules, and testing strategy. |
| G70 | Provider-neutral local workspace provider contract hardening. | Fill gaps around explicit provider IDs and deterministic local provider behavior without changing AgentKit Core. |
| G71 | Action routing through workspace providers. | Ensure file, patch, shell, validation, and policy paths route through the selected workspace binding/provider. |
| G72 | Sandbox MVP provider capability detection. | Add optional Docker/fake provider capability checks and deterministic fallback tests. |
| G73 | Workspace lifecycle API and durable metadata hardening. | Stabilize provision, cleanup, archive, manifest, retention, and safe metadata handling. |
| G74 | Console and observability workspace integration. | Add or verify workspace fields/views without high-cardinality Prometheus labels or raw content leaks. |
| G75 | Dogfood/demo workspace provider path. | Add deterministic workspace-provider demo evidence using temp/fake providers and optional Docker notes. |
| G76 | Final smoke and report. | Run practical regression checks and publish `IMPLEMENTATION_REPORT.md`. |

## G68_WORKSPACE_PROVIDER_CURRENT_STATE_MAP

### Before

- Goal id: G68_WORKSPACE_PROVIDER_CURRENT_STATE_MAP
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `docs/workspace_provider/CURRENT_STATE.md`
- Verification commands:
  - Run from `coding-agent/`.
  - `test -f docs/workspace_provider/GOAL_PROGRESS.md`
  - `test -f docs/workspace_provider/CURRENT_STATE.md`
  - `rg -n "G68|G76|WorkspaceProvider|ExecutionBinding|Docker|No production credentials" docs/workspace_provider`
  - `git diff --check -- .`
- Stop criteria:
  - Existing workspace provider state cannot be mapped without changing
    production code.
  - A deterministic G69-G76 path cannot be described without production
    credentials, hosted services, or Docker-only tests.

### After

Status: passed local verification; in PR #276.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `docs/workspace_provider/CURRENT_STATE.md`
- Tests run:
  - Run from `coding-agent/`.
  - `test -f docs/workspace_provider/GOAL_PROGRESS.md`
  - `test -f docs/workspace_provider/CURRENT_STATE.md`
  - `rg -n "G68|G76|WorkspaceProvider|ExecutionBinding|Docker|No production credentials" docs/workspace_provider`
  - `git diff --check -- .`
- Results:
  - All commands passed.
- Remaining risks:
  - The repository already has substantial Docker workspace provider code, so
    G69 must define the MVP boundary carefully to avoid duplicate abstraction.
  - Later goals still need implementation-level proof that workspace-provider
    routing preserves action safety and does not require Docker for all tests.
- Review notes:
  - Local review found that the first current-state draft overstated Developer
    Console workspace visibility and omitted several workspace lifecycle/archive
    endpoints. The map now identifies the missing dedicated console view and
    lists the additional endpoints.

## G69_WORKSPACE_PROVIDER_BOUNDARY_ADR

### Before

- Goal id: G69_WORKSPACE_PROVIDER_BOUNDARY_ADR
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- Verification commands:
  - Run from `coding-agent/`.
  - `test -f docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
  - `rg -n "WorkspaceProvider|sandbox|Docker|Prometheus|raw prompt|Acceptance Criteria" docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
  - `git diff --check -- .`
- Stop criteria:
  - Workspace provider boundaries require moving provider-specific behavior into
    AgentKit Core.
  - The sandbox MVP cannot be defined without requiring Docker for every test,
    production credentials, hosted services, or schedule/desktop/bridge work.

### After

Status: passed local verification; in PR #277.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- Tests run:
  - Run from `coding-agent/`.
  - `test -f docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
  - `rg -n "WorkspaceProvider|sandbox|Docker|Prometheus|raw prompt|Acceptance Criteria" docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
  - `git diff --check -- .`
- Results:
  - All commands passed.
- Remaining risks:
  - Later goals still need executable proof that action tools route through the
    selected workspace binding and that Docker remains optional.
  - Console and metrics additions must keep workspace identifiers out of
    Prometheus labels and raw workspace content out of rendered pages.
- Review notes:
  - Local review found that the ADR needed to carry forward the ADR-0025
    provider-instance fail-closed rule and the ADR-0021 session/admin ownership
    boundary for workspace lifecycle routes. Both boundary rules and matching
    acceptance criteria were added before merge.

## G70_PROVIDER_NEUTRAL_LOCAL_WORKSPACE_PROVIDER_CONTRACT

### Before

- Goal id: G70_PROVIDER_NEUTRAL_LOCAL_WORKSPACE_PROVIDER_CONTRACT
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/execution_binding.py`
  - `src/coding_agent/ui/schemas.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_execution_binding.py`
  - `tests/ui/test_http_server.py`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_execution_binding.py -v`
  - `uv run pytest tests/ui/test_http_server.py -k "execution_binding or workspace_provider_metadata" -v`
  - `git diff --check -- .`
- Stop criteria:
  - Provider metadata requires changing AgentKit Core or rewriting the pipeline.
  - Deterministic local provider behavior cannot be represented without Docker,
    hosted services, production credentials, or raw sensitive workspace content.

### After

Status: passed local verification; in PR #278.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/execution_binding.py`
  - `src/coding_agent/ui/schemas.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_execution_binding.py`
  - `tests/ui/test_http_server.py`
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_execution_binding.py -v`
  - `uv run pytest tests/ui/test_http_server.py -k "execution_binding or workspace_provider_metadata" -v`
  - `uv run pytest tests/ui/test_session_manager_public_api.py -k "execution_binding" -v`
  - `uv run ruff format --check src/coding_agent/ui/execution_binding.py src/coding_agent/ui/schemas.py src/coding_agent/ui/http_server.py tests/ui/test_execution_binding.py tests/ui/test_http_server.py`
  - `uv run ruff check src/coding_agent/ui/execution_binding.py src/coding_agent/ui/schemas.py src/coding_agent/ui/http_server.py tests/ui/test_execution_binding.py tests/ui/test_http_server.py`
  - `git diff --check -- .`
- Results:
  - New provider metadata tests first failed against the previous binding
    contract, then passed after implementation.
  - Final targeted verification passed.
- Remaining risks:
  - G71 still needs proof that action tools route through the selected binding.
  - G73 still needs lifecycle fail-closed provider-instance checks beyond
    binding metadata round-trip.
- Review notes:
  - Local review found that HTTP schemas accepted whitespace-only provider
    metadata while binding deserialization rejected it on reload. Schema
    validation now rejects blank-after-strip metadata and `/sessions` has a
    regression test for both provider metadata fields.

## G71_ACTION_ROUTING_THROUGH_WORKSPACE_PROVIDERS

### Before

- Goal id: G71_ACTION_ROUTING_THROUGH_WORKSPACE_PROVIDERS
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `tests/coding_agent/test_workspace_action_routing.py`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/coding_agent/test_workspace_action_routing.py -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "cloud_environment_from_execution_binding" -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
  - `git diff --check -- .`
- Stop criteria:
  - Action routing requires rewriting AgentKit Core or changing action safety
    semantics.
  - File, patch, or shell routing cannot be verified without Docker, hosted
    services, production credentials, or raw command/file content in durable
    records, metrics, traces, or docs.
  - Validation requires changing `ValidationRunner` semantics instead of
    preserving the existing local subprocess validation contract.

### After

Status: passed local verification; in PR #279.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `tests/coding_agent/test_workspace_action_routing.py`
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/coding_agent/test_workspace_action_routing.py -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "cloud_environment_from_execution_binding" -v`
  - `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
  - `uv run ruff format --check tests/coding_agent/test_workspace_action_routing.py`
  - `uv run ruff check tests/coding_agent/test_workspace_action_routing.py`
  - `git diff --check -- .`
- Results:
  - All commands passed.
  - The new contract test verifies the selected `CloudWorkspaceBinding` is
    resolved to a workspace client and that file read/write/replace, glob, grep,
    patch, and shell command tools execute against that selected client.
- Remaining risks:
  - ValidationRunner still executes local subprocess commands directly by
    design; G71 preserves that action-safety behavior instead of routing
    validation through provider clients.
- Review notes:
  - Local review found the first test only proved direct environment injection
    and that the ledger overclaimed validation routing. The test now starts from
    `CloudWorkspaceBinding` resolution, and the ledger explicitly scopes
    validation out of provider-client routing for this goal.

## G72_SANDBOX_MVP_PROVIDER_CAPABILITY_DETECTION

### Before

- Goal id: G72_SANDBOX_MVP_PROVIDER_CAPABILITY_DETECTION
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/environment/workspace_provider.py`
  - `src/coding_agent/environment/docker_workspace_provider.py`
  - `tests/ui/test_execution_binding.py`
  - `tests/coding_agent/environment/test_docker_workspace_provider.py`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_execution_binding.py -k "capabilities" -v`
  - `uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -k "capabilities or readiness" -v`
  - `uv run ruff format --check src/coding_agent/environment/workspace_provider.py src/coding_agent/environment/docker_workspace_provider.py tests/ui/test_execution_binding.py tests/coding_agent/environment/test_docker_workspace_provider.py`
  - `uv run ruff check src/coding_agent/environment/workspace_provider.py src/coding_agent/environment/docker_workspace_provider.py tests/ui/test_execution_binding.py tests/coding_agent/environment/test_docker_workspace_provider.py`
  - `git diff --check -- .`
- Stop criteria:
  - Capability detection requires Docker for all tests or external hosted
    services.
  - Docker unavailability cannot be represented as deterministic provider
    metadata without changing workspace/action semantics.

### After

Status: passed local verification; in PR #280.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/environment/__init__.py`
  - `src/coding_agent/environment/workspace_provider.py`
  - `src/coding_agent/environment/docker_workspace_provider.py`
  - `tests/ui/test_execution_binding.py`
  - `tests/coding_agent/environment/test_docker_workspace_provider.py`
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_execution_binding.py -k "capabilities" -v`
  - `uv run pytest tests/ui/test_execution_binding.py -v`
  - `uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -k "capabilities or readiness" -v`
  - `uv run ruff format --check src/coding_agent/environment/workspace_provider.py src/coding_agent/environment/docker_workspace_provider.py src/coding_agent/environment/__init__.py tests/ui/test_execution_binding.py tests/coding_agent/environment/test_docker_workspace_provider.py`
  - `uv run ruff check src/coding_agent/environment/workspace_provider.py src/coding_agent/environment/docker_workspace_provider.py src/coding_agent/environment/__init__.py tests/ui/test_execution_binding.py tests/coding_agent/environment/test_docker_workspace_provider.py`
  - `git diff --check -- .`
- Results:
  - All commands passed.
  - Added provider capability reporting with deterministic fallback to existing
    readiness for providers that do not implement a dedicated reporter.
  - Docker capability reporting returns low-cardinality ready/unavailable
    reasons and does not require a Docker daemon in tests.
- Remaining risks:
  - G73 must apply provider-instance fail-closed semantics to lifecycle APIs and
    durable metadata paths.

## G73_WORKSPACE_LIFECYCLE_API_AND_DURABLE_METADATA_HARDENING

### Before

- Goal id: G73_WORKSPACE_LIFECYCLE_API_AND_DURABLE_METADATA_HARDENING
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_http_server.py`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_http_server.py -k "foreign_provider_instance or local_durable_record or provider_404 or durable_cloud_workspace_gc" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "workspace" -v`
  - `uv run ruff format --check src/coding_agent/ui/http_server.py tests/ui/test_http_server.py`
  - `uv run ruff check src/coding_agent/ui/http_server.py tests/ui/test_http_server.py`
  - `git diff --check -- .`
- Stop criteria:
  - Lifecycle hardening requires changing durable runtime semantics or bypassing
    existing admin/session authorization.
  - Provider-local cleanup/archive behavior cannot fail closed for non-local
    provider instances without requiring Docker or hosted services.

### After

Status: passed local verification; in PR #281.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_http_server.py`
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_http_server.py -k "foreign_provider_instance or local_durable_record or durable_cloud_workspace_gc" -v`
  - `uv run pytest tests/ui/test_session_manager_runtime.py -k "workspace" -v`
  - `uv run ruff format --check src/coding_agent/ui/http_server.py tests/ui/test_http_server.py`
  - `uv run ruff check src/coding_agent/ui/http_server.py tests/ui/test_http_server.py`
  - `git diff --check -- .`
- Results:
  - All commands passed.
  - Durable-retention workspace cleanup and workspace-scoped archive endpoints
    now load the durable workspace record and fail closed when the record belongs
    to a different provider instance.
  - Provider 404 during durable workspace cleanup marks the durable record
    `lost` instead of leaving it in `cleaning`.
- Remaining risks:
  - G74 still needs safe console/observability workspace visibility, including
    avoiding workspace ids in Prometheus labels.
- Review notes:
  - Local review found that provider 404 after the `cleaning` transition could
    strand a durable record. The KeyError path now marks the record `lost` and a
    focused regression covers it.

## G74_CONSOLE_AND_OBSERVABILITY_WORKSPACE_VISIBILITY

### Before

- Goal id: G74_CONSOLE_AND_OBSERVABILITY_WORKSPACE_VISIBILITY
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_developer_console.py`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_developer_console.py -k "workspace or console_shell_routes_render_navigation_without_secrets or developer_console_e2e" -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
  - `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
  - `git diff --check -- .`
- Stop criteria:
  - Console workspace visibility requires exposing raw prompt/content/message,
    command output, stdout/stderr/env, or secrets.
  - Workspace observability requires high-cardinality Prometheus labels such as
    workspace_id, run_id, session_id, file_path, or command.
  - The change requires schedule, sandbox, desktop, bridge, proactive-agent, or
    multi-agent work.

### After

Status: merged via PR #282.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `src/coding_agent/ui/developer_console.py`
  - `src/coding_agent/ui/http_server.py`
  - `tests/ui/test_developer_console.py`
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/ui/test_developer_console.py -k "workspace or console_shell_routes_render_navigation_without_secrets or developer_console_e2e" -v`
  - `uv run pytest tests/ui/test_developer_console.py -v`
  - `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
  - `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
  - `git diff --check -- .`
- Results:
  - All commands passed.
  - Added `/console/workspaces` with navigation, provider capability summary,
    and sanitized workspace inventory from existing workspace APIs/stores.
  - Verified HTTP metrics for the console workspace route use the stable route
    label and do not include workspace ids or `workspace_id` labels.
- Remaining risks:
  - G75 still needs dogfood/demo evidence for the workspace provider path.

## G75_DOGFOOD_DEMO_WORKSPACE_PROVIDER_PATH

### Before

- Goal id: G75_DOGFOOD_DEMO_WORKSPACE_PROVIDER_PATH
- Intended files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `docs/workspace_provider/RUN_EVIDENCE.md`
  - `docs/workspace_provider/DEMO_PATH.md`
  - `tests/dogfood/test_workspace_provider_demo.py`
- Verification commands:
  - Run from `coding-agent/`.
  - `uv run pytest tests/dogfood/test_workspace_provider_demo.py -v`
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
  - `uv run ruff format --check tests/dogfood/test_workspace_provider_demo.py`
  - `uv run ruff check tests/dogfood/test_workspace_provider_demo.py`
  - `git diff --check -- .`
- Stop criteria:
  - Workspace provider dogfood cannot collect run_id-level evidence without
    production credentials, hosted services, or Docker as a mandatory test
    dependency.
  - The demo path requires raw prompt/content/message/result/secret/text,
    command output, stdout/stderr/env, file contents, or patch contents in
    durable evidence, docs, metrics, traces, or console pages.
  - The path requires schedule, desktop, bridge, proactive-agent, or multi-agent
    work.

### After

Status: passed local verification; pending PR.

- Changed files:
  - `docs/workspace_provider/GOAL_PROGRESS.md`
  - `docs/workspace_provider/RUN_EVIDENCE.md`
  - `docs/workspace_provider/DEMO_PATH.md`
  - `tests/dogfood/test_workspace_provider_demo.py`
- Dogfood evidence:
  - Recorded in `docs/workspace_provider/RUN_EVIDENCE.md`.
  - `session_id`: `9ba40953-dfdf-403c-b6dc-5e72fc62fbf1`
  - `run_id`: `3ad60c7953a74d84a63adc217286212a`
  - `workspace_id`: `ws-dogfood`
  - provider: `docker`
  - provider instance: `dogfood-local`
- Tests run:
  - Run from `coding-agent/`.
  - `uv run pytest tests/dogfood/test_workspace_provider_demo.py -v`
  - `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`
  - `uv run ruff format --check tests/dogfood/test_workspace_provider_demo.py`
  - `uv run ruff check tests/dogfood/test_workspace_provider_demo.py`
  - `git diff --check -- .`
- Results:
  - Workspace provider dogfood replay passed without Docker, hosted services,
    production credentials, or real external LLM calls.
  - The replay created an explicit cloud execution binding, persisted durable
    workspace metadata, routed representative tools through the fake workspace
    client, rendered workspace/run/observability console pages, and verified
    workspace ids did not enter Prometheus exposition.
  - Local review feedback narrowed the evidence wording to committed evidence
    and rendered console pages, and the test now injects a secret sentinel into
    non-rendered workspace metadata.
- Remaining risks:
  - Live Docker provider demonstration remains optional and environment
    dependent; deterministic proof uses a fake provider/client.
  - G76 still needs final smoke and phase implementation report.
