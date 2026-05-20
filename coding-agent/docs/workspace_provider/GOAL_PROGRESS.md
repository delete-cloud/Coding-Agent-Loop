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
