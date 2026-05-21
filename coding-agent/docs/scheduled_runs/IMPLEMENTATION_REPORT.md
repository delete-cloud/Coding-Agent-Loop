# Topic-aware Scheduled Runs Implementation Report

## Summary

G85-G92 completed the Topic-aware Scheduled Runs / Proactive Signals phase.

The implementation adds durable schedule, trigger, and proactive signal records; deterministic bounded planners; topic-aware launch preparation; read-only Developer Console visibility; Prometheus label safety for schedule/signal metadata; and final smoke coverage.

The phase does not add an autonomous scheduler loop, Bee workflow runtime, DAG executor, external executor, desktop app, bridge, or multi-agent task graph.

## Landed Goals

- G85: Current state map in `docs/scheduled_runs/CURRENT_STATE.md`.
- G86: ADR-0040 accepted in `docs/adr/0040-topic-aware-scheduled-runs.md`.
- G87: Durable schedule, trigger, and proactive signal store layer.
- G88: Bounded due schedule planner that records launch intents without executing runs.
- G89: Topic-aware launch preparation through existing Topic lifecycle APIs.
- G90: Proactive signal planner with dedupe/cooldown and synthetic signal triggers.
- G91: `/console/schedules` plus safe Prometheus schedule/signal labels.
- G92: Final smoke test, usage docs, implementation report, and regression verification.

## Key Files

- `src/coding_agent/scheduled_runs.py`
- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/observability.py`
- `tests/coding_agent/test_scheduled_runs.py`
- `tests/ui/test_developer_console.py`
- `tests/coding_agent/test_observability.py`
- `docs/scheduled_runs/USAGE.md`

## Safety Boundaries

- Planners accept explicit clock values and max bounds.
- Planners record triggers and return launch intents; they do not execute tools or launch direct action paths.
- Launch preparation creates or continues Topics before normal durable run creation.
- Scheduled/proactive work must continue through the existing runtime/session path to preserve approval, HITL, command policy, workspace policy, and action safety.
- Prometheus metrics allow low-cardinality labels only and reject IDs such as `schedule_id`, `signal_id`, `topic_id`, `run_id`, and `session_id`.
- Console pages render safe summaries and metadata, not raw prompt/content/message/result/secret/text/command output/stdout/stderr/env.

## Verification Summary

Focused phase checks passed:

- `uv run pytest tests/coding_agent/test_scheduled_runs.py -v`
- `uv run pytest tests/coding_agent/test_topic_layer_smoke.py -v`
- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_platform_smoke.py -v`
- `uv run ruff format --check tests/coding_agent/test_scheduled_runs.py`
- `uv run ruff check tests/coding_agent/test_scheduled_runs.py`
- `git diff --check -- .`

Cross-phase regression checks were run where practical and are recorded in `docs/scheduled_runs/GOAL_PROGRESS.md`.

## Remaining Work

- Add a bounded scheduler service/worker only in a future phase if explicitly requested.
- Add Bee workflow, DAG, task manifest, external executor, desktop, bridge, or multi-agent integration only in later scoped phases.
- Wire real production scheduling deployments separately from this deterministic planning foundation.

