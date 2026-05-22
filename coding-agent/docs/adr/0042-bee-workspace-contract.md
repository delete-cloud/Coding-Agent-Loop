# ADR-0042: Define Bee workspace template and run artifact contract

**Status**: Accepted
**Date**: 2026-05-22

## Context

ADR-0041 defines Bee as a generic Coding Agent task/workflow profile built on Topic and normal durable runs. The current runtime can parse sanitized Bee manifests, persist task and node records, write safe topic anchors, plan dependency-ready nodes, build safe launch metadata, render console summaries, and expose low-cardinality metrics.

The next layer is workspace-local Bee support. Users need templates under `.bee/templates/<template_id>/` and run artifacts under `.bee/runs/<task_id-or-slug>/` so local repositories can dogfood repeatable workflows without production credentials, hosted services, Docker, Kubernetes, Argo, external executors, or homelab-specific logic.

The main risk is turning workspace files into an executor that bypasses durable runs, HITL, approval policy, command policy, workspace policy, path policy, validation policy, or action safety. The second risk is leaking raw prompt/content/message/result/secret/text/command output/stdout/stderr/env through `task.json`, reports, memory candidates, traces, metrics, or console pages.

## Decision

Keep Bee workspace support in `src/coding_agent/` as a product-layer contract. AgentKit Core remains generic and should not gain Bee workspace primitives.

Define workspace templates as declarative local inputs:

```text
.bee/templates/<template_id>/
  metadata.yaml or metadata.json
  SKILL.md
  features/*.feature
  commands.yaml optional
```

- `template_id` is a safe local identifier and may appear in workspace files, durable records, console routes, and safe trace correlation attributes.
- `metadata.yaml` or `metadata.json` is the only structured template metadata source.
- `SKILL.md` and `features/*.feature` are template documentation and acceptance intent inputs. They are not executable runtime instructions by themselves.
- Template metadata should map into the existing sanitized `BeeTaskManifest` shape rather than creating a second Bee manifest model.
- Metadata may carry safe task kind/profile/title/summary, topic hints, context profile, validation profile, workspace policy hints, and node definitions.
- Metadata must not contain raw prompt/content/message/result/secret/text/command output/stdout/stderr/env.

Define workspace run artifacts as sanitized mirrors of durable Bee state:

```text
.bee/runs/<task_id-or-slug>/
  task.json
  report.md
  evidence/
  memory_candidates.yaml optional
```

- `task.json` mirrors the durable identity chain: `task_id`, `template_id`, `topic_id`, `status`, nodes, node attempts, run IDs, action IDs, validation IDs, report path, and optional memory candidate path.
- `task.json` is not the source of truth for task state. `PGBeeTaskStore` and normal durable run/action/validation stores remain authoritative where available.
- `task.json` must not store raw prompt/content/message/result/secret/text/command output/stdout/stderr/env.
- `report.md` is a sanitized human-readable summary. It may include bounded safe titles, statuses, IDs, paths relative to the `.bee/runs/<task>/` directory, evidence labels, and validation summaries.
- `evidence/` stores safe local evidence artifacts by reference. It must not become a dump of raw command output or secrets.
- `memory_candidates.yaml` is optional and stores sanitized candidate metadata only. Memory candidates remain reviewable references and must not become system instructions.

Define `commands.yaml` as a non-executing command intent contract:

- `commands.yaml` may declare named command intents, profiles, policy hints, expected validation labels, and low-cardinality categories.
- `commands.yaml` must not be executed directly by the Bee workspace loader or planner.
- Any later command execution must create normal durable runs/actions and pass existing HITL, approval policy, command policy, workspace policy, path policy, validation policy, and workspace binding gates.
- Command strings or raw shell snippets must not be rendered into traces, metrics, durable metadata, `task.json`, reports, memory candidates, or console pages.

Define path and provider boundaries:

- Workspace Bee paths are relative to the selected workspace root.
- Local dogfood tests should use temp directories and fixture workspaces.
- Docker, Kubernetes, Argo, hosted services, production credentials, and external executors are not required for this contract.
- Provider-specific behavior remains behind existing workspace provider and execution binding abstractions.

Define observability and console boundaries:

- Metrics may use low-cardinality labels such as template kind/profile/status, task kind/profile/status, node kind/profile/status, and artifact status.
- Metrics must not use `template_id`, `task_id`, `topic_id`, `run_id`, `session_id`, `node_id`, workspace IDs, file paths, commands, prompt, content, or secret as labels.
- Console pages may render safe template IDs, task IDs, node IDs, topic IDs, statuses, timestamps, safe summaries, policy decisions, validation status, and artifact paths.
- Console pages must not render raw prompt/content/message/result/secret/text/command output/stdout/stderr/env.

## Alternatives Rejected

- Add Bee workspace primitives to AgentKit Core. Rejected because Bee workspace support is a Coding Agent product/runtime contract.
- Treat `.bee/templates` as an external executor manifest. Rejected because it would bypass durable runs and safety policies.
- Execute `commands.yaml` directly. Rejected because command execution must go through existing action safety and workspace binding gates.
- Make `task.json` authoritative. Rejected because durable Bee stores and normal durable runtime stores are the source of truth.
- Hard-code homelab templates or integrations. Rejected because this phase is generic and homelab templates are developed separately.
- Store raw command output or raw model/user text in run artifacts. Rejected because it violates the privacy/no-leak contract.
- Use template/task/node/topic/run/session IDs as Prometheus labels. Rejected because they are high-cardinality identifiers.

## Acceptance Criteria

- [x] `test_bee_workspace_discovers_template_metadata`
- [x] `test_bee_workspace_rejects_sensitive_template_fields`
- [ ] `test_bee_workspace_builds_manifest_with_existing_parser`
- [ ] `test_bee_workspace_writes_safe_task_json`
- [ ] `test_bee_workspace_rejects_raw_report_and_memory_candidate_fields`
- [ ] `test_bee_workspace_commands_yaml_is_non_executing_intent`
- [ ] `test_console_bee_workspace_renders_safe_artifact_summary`
- [ ] `test_bee_workspace_metrics_do_not_use_template_or_task_ids`
- [ ] `test_bee_workspace_local_dogfood_smoke`
- [ ] `uv run pytest tests/coding_agent/test_bee_workspace.py -v`
- [ ] `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/bee_workspace/CURRENT_STATE.md`
- `docs/bee_workspace/GOAL_PROGRESS.md`
- `docs/adr/0041-bee-workflow-task-runtime.md`
- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/adr/0037-developer-console-debug-ui.md`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
- `tests/coding_agent/test_bee_runtime.py`
