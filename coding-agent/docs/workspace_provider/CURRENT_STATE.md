# Workspace Provider / Sandbox MVP Current State

Date: 2026-05-21

## Purpose

G68 maps the current workspace provider and sandbox-related implementation
before the G69-G76 MVP work. This goal is documentation-only and does not change
production code.

## Completed Baseline

- Durable Runtime G00-G11 is complete.
- Context System + Evaluation G12-G24 is complete.
- Action Safety + Workspace Execution G25-G37 is complete.
- Release Hardening + Contract Stabilization G38-G45 is complete.
- Observability Platform G46-G53 is complete.
- Developer Console / Debug UI G54-G63 is complete.
- Dogfood + Demo Readiness G64-G67 is complete.
- Current main baseline is `fc1b1478a8566b0df6133638e71ace4491f597e9` or newer.

## Existing Workspace Boundaries

AgentKit Core already depends on generic environment abstractions rather than
Coding Agent provider details:

- `src/agentkit/environment/protocols.py` defines the generic environment
  protocol and workspace summary shape.
- `src/coding_agent/environment/local.py` implements local filesystem and shell
  tools behind `LocalEnvironment`.
- `src/coding_agent/environment/cloud.py` implements `CloudEnvironment` over a
  provider-neutral `CloudWorkspaceClient` protocol.
- `src/coding_agent/plugins/core_tools.py` registers file, patch, shell,
  planner, web search, and subagent tools from the selected environment.

This means G69-G76 should harden and complete Coding Agent provider wiring
without moving provider-specific behavior into AgentKit Core.

## Existing Execution Binding

The HTTP/session layer already persists workspace binding state:

- `src/coding_agent/ui/execution_binding.py`
  - `LocalExecutionBinding`
  - `CloudWorkspaceBinding`
  - `ExecutionBinding.from_dict`
- `src/coding_agent/ui/binding_resolver.py`
  - resolves local bindings to `LocalEnvironment`
  - resolves cloud bindings to `CloudEnvironment` when a cloud client factory is
    configured
  - raises a typed error when cloud resolution is unavailable
- `src/coding_agent/ui/session_manager.py`
  - creates local bindings from `repo_path`
  - accepts explicit execution bindings
  - persists session workspace metadata
  - uses `BindingResolver` before constructing the runtime pipeline

The current binding kind set is `local` and `cloud`. G69 should decide whether
"workspace provider" remains represented by these binding kinds plus provider
metadata, or whether a new app-level provider descriptor is needed.

## Existing Workspace Provider Protocol

`src/coding_agent/environment/workspace_provider.py` already defines a
provider registry and `WorkspaceProvider` protocol with methods for:

- building cloud clients
- readiness checks
- provisioning and cleanup
- archive import/export
- stale workspace cleanup
- inventory and lookup
- archive manifests
- diffs and patches
- branch publication

The naming is still cloud-oriented even when the concrete provider is Docker.
G69 should decide whether to rename, wrap, or document this as app-level
workspace-provider terminology without broad semantic churn.

## Existing Docker Sandbox Provider

`src/coding_agent/environment/docker_workspace_provider.py` already implements a
Docker-backed provider with:

- workspace ID validation
- container name generation
- workspace root isolation
- file read/write/replace, glob, grep, patch, and command execution
- env allowlist handling for command execution
- timeouts and cleanup
- archive import/export
- inventory, cleanup, manifest, diff, patch, and branch publication helpers
- image allowlist, resource limits, network mode, and active workspace quota

Tests under `tests/coding_agent/environment/test_docker_workspace_provider.py`
use fake command runners and temp directories for deterministic coverage. The
phase must continue not to require Docker for every test.

## Existing HTTP And Session Surfaces

The FastAPI layer already exposes workspace-related endpoints in
`src/coding_agent/ui/http_server.py`, including:

- `POST /sessions` with `repo_path`, `execution_binding`, and
  `workspace_source`
- `GET /sessions/{session_id}/workspace/archive/manifest`
- `GET /sessions/{session_id}/workspace/archive`
- `GET /sessions/{session_id}/workspace`
- `GET /sessions/{session_id}/workspace/diff`
- `GET /sessions/{session_id}/workspace/patch`
- `POST /sessions/{session_id}/publish`
- `GET /workspaces`
- `POST /workspaces/gc`
- `GET /workspaces/{workspace_id}`
- `POST /workspaces/{workspace_id}/retain`
- `POST /workspaces/{workspace_id}/pin`
- `POST /workspaces/{workspace_id}/unpin`
- `DELETE /workspaces/{workspace_id}`
- `GET /workspaces/{workspace_id}/archive/manifest`
- `GET /workspaces/{workspace_id}/archive`

The existing tests cover many of these routes in `tests/ui/test_http_server.py`
and `tests/ui/test_http_server_workspace_transfer.py`.

## Existing Console Surface

Developer Console G54-G63 currently focuses on sessions, runs, interactions,
tape, context, memory, actions, observability, and release. It does not yet have
a dedicated workspace-provider page.

G74 should add or verify safe workspace-provider visibility without rendering
raw file contents, patch contents, command output, stdout, stderr, environment
values, credentials, or unsafe URLs.

## Existing Observability And Metrics Rules

The observability platform already requires low-cardinality Prometheus labels
and fail-open metrics behavior. This phase must not add high-cardinality labels
such as `run_id`, `session_id`, `workspace_id`, `file_path`, `command`, prompt,
content, or secret.

Workspace identifiers may appear in durable records or console pages when they
are part of existing sanitized API contracts, but not as Prometheus labels.

## Existing Tests To Preserve

Relevant deterministic tests include:

- `uv run pytest tests/ui/test_execution_binding.py -v`
- `uv run pytest tests/coding_agent/environment/test_local_environment.py -v`
- `uv run pytest tests/coding_agent/environment/test_cloud_environment.py -v`
- `uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "workspace or binding" -v`
- `uv run pytest tests/ui/test_http_server_workspace_transfer.py -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/dogfood/test_local_dogfood_run.py -v`

Regression baseline checks from G00-G67 remain relevant where touched code
overlaps durable runtime, context, action safety, observability, console, and
dogfood paths.

## G69-G76 Implementation Guidance

- Prefer app-layer changes in `src/coding_agent/`.
- Keep AgentKit Core provider-neutral.
- Treat Docker as optional for runtime and tests; use fake command runners and
  temp directories for deterministic coverage.
- Do not introduce schedules, desktop app, bridge, proactive-agent, or
  multi-agent task graph work.
- Do not require production credentials or hosted services.
- Avoid storing or rendering raw prompt, message, model result text, command
  output, stdout, stderr, environment values, secrets, file contents, or patch
  contents in docs, traces, metrics, durable records, or console pages.
- Add abstractions only when they remove real duplication or clarify the
  existing local/cloud/Docker ownership boundary.

## Current Assessment

The repository already contains substantial workspace provider and Docker
sandbox work. The MVP phase should therefore focus on boundary documentation,
provider-neutral naming/config hardening, deterministic fallback paths,
workspace action routing proofs, safe console/observability integration, and a
final smoke/report rather than rebuilding the provider stack.
