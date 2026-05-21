# ADR-0040: Define topic-aware scheduled runs and proactive signal boundaries

**Status**: Accepted
**Date**: 2026-05-21

## Context

The repository now has durable sessions, durable runs, action safety, workspace providers, observability, Developer Console pages, and a product-level Topic layer over tape ranges. The next capability is topic-aware scheduled runs and proactive signals. These should make it possible to continue known work at bounded times or in response to safe local signals, while still creating ordinary durable runs.

The main risk is accidentally creating a second execution path that bypasses HITL, approval policy, command policy, workspace policy, action safety, or durable run semantics. The second risk is turning schedules into unbounded autonomous loops. The third risk is leaking raw prompts, messages, command output, stdout, stderr, env, or secrets into durable records, traces, metrics, docs, or console pages. The fourth risk is treating schedules as Bee workflows or AgentKit Core runtime primitives.

## Decision

Keep scheduled runs and proactive signals as Coding Agent product/runtime orchestration, not AgentKit Core primitives.

- AgentKit Core continues to own generic runtime, tape, and observability abstractions.
- Coding Agent owns schedule records, proactive signal records, trigger planning, topic binding, run launch intents, console pages, and app-level observability wiring.
- Scheduled/proactive work must create or continue a Topic first, then create a normal durable run through existing Coding Agent runtime/session paths.
- Scheduled/proactive work must not call tools directly and must not bypass HITL, approval policy, command policy, workspace policy, action safety, runtime store writes, or workspace execution bindings.

Define a Scheduled Run as a durable product record that describes bounded intent to launch ordinary runs.

- A schedule has a `schedule_id`, optional `topic_id`, `session_id`, cadence or one-shot trigger metadata, status, owner, created/updated timestamps, next due time, and safe metadata.
- `schedule_id` and `topic_id` may appear in durable product records, console routes, and safe trace correlation attributes.
- `schedule_id` and `topic_id` must not be Prometheus labels.
- Schedule metadata must be JSON-safe and must not include raw prompts, raw messages, raw content, raw command output, stdout, stderr, env, secrets, or unbounded text.
- A schedule is not a Bee workflow, DAG node, task manifest, external executor, desktop automation, bridge, or multi-agent task graph.

Define a Proactive Signal as a durable product record that may be planned into a scheduled run intent.

- A signal has a `signal_id`, kind, status, optional topic/session linkage, deduplication key, observed time, safe summary, and safe metadata.
- Signals are evidence for scheduling decisions. They are not instructions and do not directly execute tools.
- Signal ingestion must support deduplication and cooldown/rate-limit behavior to avoid unbounded autonomous loops.
- Signal summaries must be sanitized and bounded.

Define trigger planning as deterministic and bounded.

- A trigger planner evaluates schedules/signals against an explicit clock value.
- Planning returns launch intents and record updates; it does not execute the agent loop directly.
- Planning must accept a maximum number of due items per invocation.
- Repeated planning of the same schedule/signal must be idempotent or deduplicated by durable state.
- Tests must use fake clocks, fake stores, fake providers, and temp workspaces where needed.

Define topic binding before run creation.

- If a schedule references an open topic, the launch intent continues that topic.
- If a schedule references a finalized/aborted/missing topic and policy allows continuation, the launch intent can create a new topic linked to the prior topic through recall metadata.
- If a signal maps to an existing open topic, the launch intent continues that topic.
- If no topic exists, schedule/proactive launch preparation creates a new Topic using `TopicLifecycle` and safe bounded title/metadata.
- Topic lifecycle anchors remain the topic layer's responsibility. Schedule/proactive records must not write high-frequency tape anchors.

Define run launch as a normal durable runtime path.

- A schedule/proactive launch produces a launch intent containing safe ids, topic metadata, session id, reason kind, and bounded metadata.
- A launch intent must be converted into a normal durable run by existing app runtime/session code.
- The launched run keeps the configured approval policy and workspace execution binding.
- Scheduled/proactive origins may appear in run metadata only as sanitized ids and low-cardinality reason/status fields.
- Raw trigger inputs, raw prompts, raw command output, stdout, stderr, env, secrets, and raw content must not be stored in run metadata.

Define observability and console boundaries.

- Metrics may use low-cardinality labels such as `schedule_kind`, `schedule_status`, `trigger_kind`, `signal_kind`, `signal_status`, `topic_kind`, and `topic_status`.
- Metrics must not use `schedule_id`, `signal_id`, `topic_id`, `run_id`, `session_id`, `workspace_id`, file path, command, prompt, content, or secret as labels.
- Trace attributes may include safe correlation ids such as schedule id, signal id, topic id, run id, and session id only when allowed by existing trace-safety rules and must never include raw content or secrets.
- Console pages may show schedule ids, signal ids, topic ids, linked run ids, statuses, timestamps, safe summaries, and low-cardinality reasons.
- Console pages must not bypass approval/action policy, and resolve/launch controls must remain bounded and policy-preserving.

## Alternatives Rejected

- Add schedules to AgentKit Core. Rejected because schedules/proactive signals are Coding Agent product orchestration, while AgentKit remains provider-neutral and generic.
- Execute tools directly from the scheduler. Rejected because it bypasses durable run, HITL, approval, command, workspace, and action-safety paths.
- Model schedules as Bee workflows now. Rejected because Bee DAG runtime, task manifests, external executors, desktop, bridge, and multi-agent graphs are out of scope for this phase.
- Store raw signal/prompt/message/command output in schedule records. Rejected because it violates the no-leak boundary and would expose sensitive content through docs, traces, durable records, and console pages.
- Use schedule ids, signal ids, topic ids, run ids, session ids, workspace ids, file paths, or commands as Prometheus labels. Rejected because they are high-cardinality or sensitive.
- Run an always-on autonomous loop in tests or production by default. Rejected because this phase requires deterministic bounded planning and must not create unbounded loops.

## Acceptance Criteria

Implementation of G87-G92 should add executable tests covering these contracts:

- [ ] `test_schedule_store_schema_is_idempotent`
- [ ] `test_schedule_store_create_update_list_and_record_trigger`
- [ ] `test_proactive_signal_store_deduplicates_signals`
- [ ] `test_schedule_planner_returns_bounded_due_launch_intents`
- [ ] `test_schedule_planner_uses_fake_clock_without_external_services`
- [ ] `test_topic_aware_launch_creates_topic_when_missing`
- [ ] `test_topic_aware_launch_continues_open_topic`
- [ ] `test_scheduled_launch_preserves_approval_policy_and_workspace_binding`
- [ ] `test_proactive_signal_cooldown_prevents_unbounded_loop`
- [ ] `test_schedule_metrics_do_not_use_schedule_id_topic_id_or_run_id_labels`
- [ ] `test_console_schedules_render_safe_summaries`
- [ ] `test_scheduled_runs_smoke_topic_signal_launch_console`
- [ ] `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
- [ ] `uv run pytest tests/ui/test_developer_console.py -v`
- [ ] `git diff --check -- .`

## References

- `docs/scheduled_runs/CURRENT_STATE.md`
- `docs/scheduled_runs/GOAL_PROGRESS.md`
- `docs/adr/0029-durable-runtime-identity.md`
- `docs/adr/0032-durable-runtime-lifecycle-statuses.md`
- `docs/adr/0035-action-safety-and-workspace-execution.md`
- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/adr/0037-developer-console-debug-ui.md`
- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- `docs/adr/0039-topic-layer-tape-view-boundaries.md`
- `src/coding_agent/runtime_store.py`
- `src/coding_agent/topic_store.py`
- `src/coding_agent/topic_lifecycle.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/observability.py`
