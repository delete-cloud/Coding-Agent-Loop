# Bee Workspace Current State

Date: 2026-05-22

G102 maps the current repository state before adding workspace-local `.bee` templates and `.bee` run artifacts. This goal is documentation-only and does not change production code.

## Summary

- The generic Bee runtime from G93-G101 exists in `src/coding_agent/bee_runtime.py`.
- Bee currently supports sanitized in-memory task manifests, durable task/node records, topic lifecycle anchors, deterministic node planning, safe launch metadata, console rendering, and low-cardinality metrics.
- There is no workspace-local `.bee/templates/<template_id>/` loader yet.
- There is no `.bee/runs/<task_id-or-slug>/` artifact writer yet.
- There is no `commands.yaml` contract parser yet.
- Developer Console has a Bee page, but it renders runtime summaries only; it does not inspect workspace template or run artifact directories.
- Workspace provider support exists, but Bee does not yet bind templates or run artifacts to provider-local paths.

## Existing Bee Runtime

Relevant files:

- `src/coding_agent/bee_runtime.py`
- `tests/coding_agent/test_bee_runtime.py`
- `docs/bee_runtime/CURRENT_STATE.md`
- `docs/bee_runtime/USAGE.md`
- `docs/bee_runtime/IMPLEMENTATION_REPORT.md`
- `docs/adr/0041-bee-workflow-task-runtime.md`

Current production shapes:

- `BeeTaskManifest` parses a safe declarative task manifest.
- `BeeNodeManifest` parses safe node intent, dependency, context profile, validation profile, and metadata fields.
- `BeeTaskRecord` and `BeeNodeRecord` model durable task/node state.
- `PGBeeTaskStore` persists `bee_tasks` and `bee_task_nodes`.
- `BeeTaskLifecycle` writes safe task start/finalize anchors into a Topic tape range.
- `BeeTaskPlanner` claims dependency-ready nodes and returns `BeeNodeLaunchIntent` records.
- `build_bee_launch_metadata()` converts a launch intent into additive metadata for normal durable runs.

Current constraints:

- The Bee parser rejects raw prompt/content/message/result/secret/text/command_output/stdout/stderr/env keys and executable keys such as command, shell, script, executor, cmd, args, and argv.
- The planner does not execute tools, create runs, call LLMs, or run commands.
- Existing metrics forbid high-cardinality labels including `task_id`, `topic_id`, `run_id`, `session_id`, and `node_id`.

Bee workspace implication:

- Workspace template loading should reuse `parse_bee_task_manifest()` rather than adding a second manifest schema.
- Workspace run artifacts should mirror durable task/node identity, not replace `PGBeeTaskStore`.
- Any command-like template data must remain declarative until routed through existing action safety and workspace binding paths.

## Existing Workspace Provider Surface

Relevant files:

- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/environment/local.py`
- `src/coding_agent/ui/execution_binding.py`
- `src/coding_agent/ui/binding_resolver.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/coding_agent/test_workspace_action_routing.py`
- `tests/dogfood/test_workspace_provider_demo.py`
- `docs/workspace_provider/CURRENT_STATE.md`
- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`

Current behavior:

- Workspace providers are app-layer Coding Agent abstractions, not AgentKit Core primitives.
- Local and cloud/Docker execution bindings already route runtime tools through the selected environment.
- Existing action safety tests prove file, patch, command, validation, and restore behavior through existing gates.
- Docker provider tests use fake command runners and temp directories; Docker is not required for all tests.

Bee workspace implication:

- `.bee/templates` and `.bee/runs` should be accessed through a workspace root/path contract, not through a new executor.
- Local template dogfood can use temp directories and local filesystem paths.
- Provider-specific execution remains out of scope; workspace-local Bee support must not require Docker, Kubernetes, Argo, or hosted services.

## Proposed Workspace Directory Contract

The user-provided target contract is:

```text
.bee/templates/<template_id>/
  metadata.yaml or metadata.json
  SKILL.md
  features/*.feature
  commands.yaml optional

.bee/runs/<task_id-or-slug>/
  task.json
  report.md
  evidence/
  memory_candidates.yaml optional
```

Current repository state:

- No production code reads `.bee/templates`.
- No production code writes `.bee/runs`.
- No tests currently create this exact workspace structure.
- `pyyaml` is already a project dependency, so YAML parsing can be implemented without adding a dependency.

Required privacy boundary:

- `SKILL.md`, feature files, reports, and memory candidates can contain user-facing prose, but runtime metadata, traces, metrics, durable records, and console summaries must not expose raw prompt/content/message/result/secret/text/command output/stdout/stderr/env.
- `task.json` must mirror IDs and low-cardinality state only. It must not store raw command output, stdout, stderr, env, secrets, or unreviewed raw model content.

## Existing Developer Console And Observability

Relevant files:

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
- `tests/ui/test_developer_console.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_observability_platform_smoke.py`

Current behavior:

- `/console/bee` already renders `ConsoleBeeTaskSummary` and `ConsoleBeeNodeSummary`.
- Console rendering escapes values and uses safe helpers for IDs/text/metadata.
- Observability supports low-cardinality Bee labels such as `task_kind`, `task_status`, `task_profile`, `node_kind`, `node_status`, and `node_profile`.
- Observability forbids `task_id` and `node_id` as Prometheus labels.

Bee workspace implication:

- Later console work can add template/run artifact summaries to the existing Bee page without introducing raw file-content rendering.
- Metrics can count template/run-artifact outcomes using low-cardinality labels such as template kind/profile/status, but not `template_id`, `task_id`, `topic_id`, `run_id`, or file paths.

## Existing Tests To Preserve

Focused checks for this phase:

- `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/dogfood/test_workspace_provider_demo.py -v`
- `git diff --check -- .`

Regression checks remain relevant where touched code overlaps durable runtime, context, action safety, topic, schedule, workspace, observability, or console paths.

## G103-G109 Suggested Target Shape

The user goal did not provide detailed per-goal names. Use this bounded sequence unless superseded:

- G103: ADR for Bee workspace contract and local template/run artifact boundaries.
- G104: Workspace template discovery and metadata parser for `.bee/templates/<template_id>/`.
- G105: Template-to-Bee manifest builder that reuses `parse_bee_task_manifest()`.
- G106: `.bee/runs/<task_id-or-slug>/task.json` and safe report/evidence artifact writer.
- G107: `commands.yaml` contract parser that records safe command intent without executing it.
- G108: Console/observability integration and deterministic local dogfood fixture.
- G109: End-to-end smoke tests, usage docs, and implementation report.

## Exact Files Likely To Modify Later

Production files:

- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_workspace.py` or equivalent new app-layer module
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/observability.py`

Test files:

- `tests/coding_agent/test_bee_runtime.py`
- `tests/coding_agent/test_bee_workspace.py` or equivalent new test file
- `tests/ui/test_developer_console.py`
- `tests/dogfood/test_workspace_provider_demo.py` or a new dogfood fixture test

Docs:

- `docs/bee_workspace/GOAL_PROGRESS.md`
- `docs/bee_workspace/CURRENT_STATE.md`
- `docs/bee_workspace/USAGE.md`
- `docs/bee_workspace/IMPLEMENTATION_REPORT.md`
- `docs/adr/0042-bee-workspace-contract.md` or equivalent ADR path

## Current Assessment

The repository already has the runtime primitives needed for generic Bee support. The missing layer is a small Coding Agent module that treats workspace-local `.bee` files as sanitized product artifacts:

- templates are loaded from local workspace directories,
- task manifests are built through the existing Bee parser,
- run artifacts mirror durable task identity,
- command declarations remain non-executing unless later routed through existing safety gates,
- console/metrics show only safe summaries.

No AgentKit Core changes appear necessary.
