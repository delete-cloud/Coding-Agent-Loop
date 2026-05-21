# Topic-aware Scheduled Runs Usage

## Scope

Topic-aware scheduled runs are a Coding Agent product/runtime layer. They plan bounded launch intents that create or continue Topics and then flow through normal durable run execution.

This phase does not add an always-on scheduler loop, Bee workflow runtime, DAG executor, external executor, desktop app, bridge, or multi-agent task graph.

## Records

- `ScheduleRecord` stores a bounded schedule for a session and optional topic.
- `ScheduleTriggerRecord` stores one planned trigger for a schedule or proactive signal.
- `ProactiveSignalRecord` stores a deduplicated signal that can be planned into a trigger after cooldown checks.
- `PreparedScheduledRun` stores safe run metadata for the later normal durable run path.

IDs such as `schedule_id`, `signal_id`, `topic_id`, `run_id`, and `session_id` may appear in durable records and console views. They must not be Prometheus labels.

## Planning Flow

1. Persist schedules and signals through `PGScheduledRunStore`.
2. Call `ScheduledRunPlanner.plan_due_schedules(now=..., max_due=...)` with an explicit clock value and bound.
3. Call `ProactiveSignalPlanner.plan_new_signals(now=..., max_signals=...)` with an explicit clock value and bound.
4. Convert each launch intent with `ScheduledRunLaunchPreparer.prepare(intent=..., tape=...)`.
5. Submit the prepared metadata through the existing normal durable run/session path.

The planners do not execute tools, create direct action records, or bypass approval, HITL, command policy, workspace policy, or action safety.

## Topic Behavior

- If an intent references an open topic on the same tape/session, launch preparation continues that topic.
- If an intent references a missing, finalized, or aborted topic, launch preparation rejects it.
- If an intent does not reference a topic, launch preparation reuses an open session/tape topic or creates a new topic through `TopicLifecycle`.
- New topics write the normal `topic_initial` tape anchor.

## Console

Open `/console/schedules` to inspect:

- schedules
- planned schedule triggers
- proactive signals

The page is read-only. It renders IDs, statuses, timestamps, safe titles, safe reasons, and safe signal summaries only.

## Observability

Allowed low-cardinality Prometheus labels include:

- `schedule_kind`
- `schedule_status`
- `trigger_kind`
- `signal_kind`
- `signal_status`

Forbidden Prometheus labels include:

- `schedule_id`
- `signal_id`
- `topic_id`
- `run_id`
- `session_id`
- `workspace_id`
- `file_path`
- `command`
- `prompt`
- `content`
- `secret`

## Verification

Use the focused phase checks:

```bash
uv run pytest tests/coding_agent/test_scheduled_runs.py -v
uv run pytest tests/ui/test_developer_console.py -v
uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_platform_smoke.py -v
git diff --check -- .
```

