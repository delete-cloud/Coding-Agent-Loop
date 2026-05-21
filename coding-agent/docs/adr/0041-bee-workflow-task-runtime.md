# ADR-0041: Define Bee workflow task runtime boundaries

**Status**: Accepted
**Date**: 2026-05-22

## Context

The repository now has durable sessions and runs, context packs, action safety, workspace providers, observability, Developer Console pages, topic ranges, and topic-aware scheduled/proactive launch preparation. The next capability is a generic Bee-style workflow template and task manifest runtime.

The main risk is building a second executor that bypasses durable run lifecycle, HITL, approval policy, command policy, workspace policy, path policy, validation policy, or action safety. The second risk is treating Bee as a homelab-specific workflow system. The third risk is storing raw prompts, messages, content, command output, stdout, stderr, env, secrets, or arbitrary executor instructions in durable records, manifests, traces, metrics, docs, reports, or console pages.

G93 also found an existing legacy `TopicPlugin` that still writes `topic_start` and `topic_end` anchors and can store a truncated first user message in a `topic_start` payload or event label. Bee work that adds topic/task anchors must deliberately account for that compatibility boundary.

## Decision

Keep Bee as a Coding Agent product/runtime profile built on Topic.

- AgentKit Core remains generic and should not gain Bee-specific primitives.
- Coding Agent owns Bee templates, task manifests, durable task records, task planning, topic/task provenance, console views, and app-level observability.
- Bee workflow execution must create ordinary durable runs through existing Coding Agent runtime/session paths.
- Bee task planning must not execute tools directly.
- Bee nodes must not bypass HITL, approval policy, command policy, workspace policy, path policy, validation policy, runtime store writes, or workspace execution bindings.

Define a Bee workflow as a bounded task plan attached to one Topic.

- A Bee workflow has a `task_id`, `topic_id`, `session_id`, kind/profile/status, title, safe summary, created/updated timestamps, and safe metadata.
- A workflow may contain ordered nodes.
- A node has a `node_id`, task linkage, kind/profile/status, optional dependency IDs, optional launch/run linkage, safe title, and safe metadata.
- `task_id`, `topic_id`, and `node_id` may appear in durable records, task manifests, console routes, and safe trace correlation attributes.
- `task_id`, `topic_id`, `node_id`, `run_id`, `session_id`, file path, command, prompt, content, and secret must not be Prometheus labels.

Define TaskManifest as a sanitized declarative input.

- Task manifests are product-level data, not executable code.
- A manifest may describe task kind/profile, safe title/summary, topic binding hints, context profile, validation profile, workspace policy hints, and node dependencies.
- A manifest must not contain raw prompt/content/message/result/secret/text/command output/stdout/stderr/env.
- A manifest must not contain arbitrary command strings that are executed by the Bee runtime.
- If later phases add command-like node requests, those requests must be translated into normal agent actions and pass existing command/workspace/action-safety gates.

Define Bee task planning as deterministic and bounded.

- A planner consumes a validated manifest and current durable task state.
- Planning returns launch intents or node state transitions; it does not run tools directly.
- Planning must accept explicit clocks and max node bounds.
- Planning must be deterministic in tests with fake stores, fake clocks, fixture manifests, temp workspaces, and fake validation results.
- A node launch intent must be converted to a normal durable run by existing app runtime/session code.

Define topic/task provenance.

- Bee workflow lifecycle can write safe product anchors in the topic tape range, for example task started and task finalized anchors.
- G97 must inspect, migrate, bypass, or explicitly supersede legacy `TopicPlugin` anchor behavior before adding Bee-specific anchors.
- Bee anchors must use existing generic tape anchor shape and must not store raw prompts/messages/command output.
- Context packs, memory evidence, eval results, action records, and validation records may reference `task_id`, `node_id`, `topic_id`, and source entry ranges in durable product records where useful.

Define observability and console boundaries.

- Metrics may use low-cardinality labels such as `task_kind`, `task_status`, `task_profile`, `node_kind`, `node_status`, and `node_profile`.
- Metrics must not use `task_id`, `node_id`, `topic_id`, `run_id`, `session_id`, `workspace_id`, file path, command, prompt, content, or secret as labels.
- Traces may include safe task/node correlation IDs only if existing no-leak attribute rules are preserved.
- Console pages may show task IDs, node IDs, topic IDs, run links, statuses, timestamps, safe summaries, policy decisions, validation status, and low-cardinality profile data.
- Console pages must not bypass policy or provide unbounded launch/resolve controls in this phase.

Keep the following explicitly out of scope:

- Homelab-specific templates or hard-coded homelab task kinds.
- NetBird, OCI, Argo CD, nmem, Kubernetes, Argo Workflows, external executor adapters, desktop app, bridge, and multi-agent task graph runtime.
- Real external LLM calls, hosted services, Docker-only tests, or production credentials.

## Alternatives Rejected

- Add Bee primitives to AgentKit Core. Rejected because Bee is a Coding Agent product/runtime workflow profile and AgentKit should remain generic.
- Execute task nodes directly from templates. Rejected because it would bypass durable runs and safety policies.
- Treat TaskManifest as an executor manifest. Rejected because this phase needs safe product intent, not arbitrary command or external executor behavior.
- Hard-code homelab Bee templates. Rejected because this phase is generic and homelab templates are separate.
- Use task, node, topic, run, or session IDs as Prometheus labels. Rejected because they are high-cardinality identifiers.
- Ignore the legacy `TopicPlugin`. Rejected because it can create conflicting topic anchors and already has a known raw-message exposure path.

## Acceptance Criteria

- [x] `test_bee_manifest_parses_safe_fixture`
- [x] `test_bee_manifest_rejects_raw_sensitive_fields`
- [x] `test_bee_store_schema_is_idempotent`
- [x] `test_bee_store_create_update_list_task_and_nodes`
- [x] `test_bee_topic_lifecycle_writes_safe_task_anchors`
- [x] `test_bee_topic_lifecycle_accounts_for_legacy_topic_plugin_boundary`
- [x] `test_bee_planner_returns_bounded_launch_intents_without_execution`
- [x] `test_bee_launch_metadata_preserves_approval_and_workspace_policy`
- [x] `test_bee_context_and_validation_metadata_is_reference_only`
- [ ] `test_bee_metrics_allow_low_cardinality_labels_without_task_or_node_ids`
- [ ] `test_console_bee_renders_safe_task_and_node_summaries`
- [ ] `test_bee_runtime_smoke_manifest_topic_launch_console_metrics`
- [x] `uv run pytest tests/coding_agent/test_bee_runtime.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `uv run pytest tests/coding_agent/test_observability.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/bee_runtime/CURRENT_STATE.md`
- `docs/bee_runtime/GOAL_PROGRESS.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/adr/0037-developer-console-debug-ui.md`
- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- `docs/adr/0040-topic-aware-scheduled-runs.md`
- `src/coding_agent/topic_store.py`
- `src/coding_agent/topic_lifecycle.py`
- `src/coding_agent/plugins/topic.py`
- `src/coding_agent/scheduled_runs.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/observability.py`
- `src/coding_agent/ui/developer_console.py`
