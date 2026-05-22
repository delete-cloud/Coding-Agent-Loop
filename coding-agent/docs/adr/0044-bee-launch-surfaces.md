# ADR-0044: Define Bee launch surfaces and scheduled/proactive integration

**Status**: Accepted
**Date**: 2026-05-23

## Context

ADR-0041 defines Bee as a generic Coding Agent task/workflow profile built on
Topic. ADR-0042 defines the workspace-local `.bee` template and run artifact
contract. ADR-0043 connects Bee command intents to local action safety,
validation, and evidence-gated completion.

The remaining gap is a stable product-layer launch surface. Today Bee tasks can
be modeled and executed through lower-level pieces, but manual launch, scheduled
launch, proactive signal launch, retry/resume/cancel/abort, console visibility,
and launch metrics are not unified.

## Decision

Keep Bee launch in `src/coding_agent/` as product-layer runtime code. AgentKit
Core remains generic and does not gain Bee launch primitives.

Define `BeeLaunchSource`:

- `manual`
- `schedule`
- `proactive_signal`

Define `BeeLaunchRequest` as a sanitized request to start or continue a Bee task:

- source
- template ID
- workspace reference
- input bindings
- topic policy
- workspace policy
- optional schedule ID or signal ID
- optional existing topic/session references
- safe metadata only

Define `BeeTemplateResolution`:

- Resolves `template_id` from workspace-local `.bee/templates`.
- Validates the template with existing Bee workspace and manifest parsers.
- Produces a `BeeTaskManifest`.
- Does not execute `commands.yaml`.

Define `BeeInputBinding`:

- Binds explicit launch inputs and template defaults.
- Rejects unknown or unsafe input names/values unless a future explicit policy
  permits them.
- Inputs are reference/config data, not prompt text or command strings.

Define `BeeTopicPolicy`:

- Launch creates or continues a Topic.
- Existing open topics may be continued when policy allows.
- New topics are created through existing topic lifecycle code.
- Launch metadata may contain safe topic correlation IDs in durable records and
  console routes, but not Prometheus labels.

Define `BeeWorkspacePolicy`:

- Launch must bind to an existing workspace provider or safe workspace root.
- Workspace policy cannot be overridden by `.bee` templates, schedules, or
  signals.
- Node execution remains workspace-bound and still goes through command bridge
  and action safety.

Define `BeeLaunchPolicy`:

- Schedules and proactive signals may request Bee launches only through the same
  launch path as manual launches.
- Launch policy never bypasses HITL, approval policy, command policy, workspace
  policy, path policy, validation policy, or evidence requirements.
- Launch creates tasks and artifacts; it does not execute arbitrary commands.
- Any node execution still goes through the Bee command bridge from ADR-0043.

Define `BeeLaunchPlan`:

- Contains resolved template, sanitized inputs, topic/workspace policy, source,
  and safe metadata.
- Does not create durable records or execute nodes.

Define `BeeLaunchResult`:

- Contains launch ID, task ID, topic ID, session ID, source, status, and safe
  error summary when applicable.
- May sync workspace-local `task.json` when artifact support is enabled.
- Does not contain raw prompt/content/message/result/secret/text/command output,
  stdout/stderr, env, or raw command strings.

Define durable launch records:

- Store launch ID, source, template ID, optional task/topic/session/workspace
  references, optional schedule/signal references, status, timestamps, safe
  error type/summary, and safe metadata.
- Launch IDs may appear in durable records, task artifacts, console routes, and
  safe trace correlation attributes.
- Launch IDs must not be Prometheus labels.

Define console and observability boundaries:

- Console may show launch list/detail, source, status, template/task/topic links,
  schedule/signal references, lifecycle-control visibility, and safe errors.
- Console must not render raw prompts, messages, command output, stdout/stderr,
  env, secrets, or raw evidence bodies.
- Metrics may use low-cardinality labels only, such as launch source/status,
  schedule launch status, proactive signal kind/status, task kind/profile/status,
  and node kind/profile/status.
- Metrics must not label by `launch_id`, `task_id`, `topic_id`, `run_id`,
  `session_id`, `node_id`, file paths, command strings, prompt, content, or
  secret values.

Keep deferred:

- External executor adapters.
- Docker/Kubernetes/Argo Workflows/Argo CD execution.
- nmem sync.
- Homelab-specific templates or infrastructure integrations.
- Desktop app, bridge app, and multi-agent task graph runtime.

## Alternatives Rejected

- Let schedules or proactive signals create Bee tasks with a separate code path.
  Rejected because duplicate launch semantics would make safety and idempotency
  harder to audit.
- Execute command intents during launch. Rejected because launch is a task
  creation surface; node execution remains policy-bound through the Bee command
  bridge.
- Put Bee launch primitives in AgentKit Core. Rejected because Bee is a Coding
  Agent product/runtime abstraction.
- Use launch/task/topic/run/session IDs as Prometheus labels. Rejected because
  they are high-cardinality identifiers.
- Hard-code homelab template behavior. Rejected because this phase must stay
  generic.

## Acceptance Criteria

- [x] `test_bee_launch_store_schema_is_idempotent`
- [x] `test_bee_launch_store_create_load_list_update_and_attach`
- [x] `test_bee_launch_plan_resolves_workspace_template`
- [x] `test_bee_launch_plan_rejects_missing_template`
- [x] `test_manual_bee_launch_creates_topic_task_and_artifact`
- [x] `test_bee_task_lifecycle_resume_retry_cancel_abort`
- [x] `test_scheduled_bee_launch_creates_task_and_links_schedule`
- [x] `test_proactive_signal_bee_launch_creates_task_and_links_signal`
- [ ] `test_console_bee_launch_renders_safe_launch_summary`
- [ ] `test_bee_launch_metrics_omit_high_cardinality_ids`
- [ ] `test_bee_launch_e2e_smoke`
- [x] `uv run pytest tests/coding_agent/test_bee_launch.py -v`
- [x] `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/bee_launch/CURRENT_STATE.md`
- `docs/bee_launch/GOAL_PROGRESS.md`
- `docs/adr/0041-bee-workflow-task-runtime.md`
- `docs/adr/0042-bee-workspace-contract.md`
- `docs/adr/0043-bee-command-bridge.md`
- `src/coding_agent/bee_runtime.py`
- `src/coding_agent/bee_workspace.py`
- `src/coding_agent/bee_command_bridge.py`
- `src/coding_agent/scheduled_runs.py`
- `src/coding_agent/topic_lifecycle.py`
- `src/coding_agent/topic_store.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
