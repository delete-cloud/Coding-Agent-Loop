# Bee Runtime Implementation Report

## Scope

G93-G101 added a generic Bee-style workflow/task runtime foundation for Coding Agent.

Bee is implemented as a Coding Agent product/runtime profile built on Topic, durable Bee task records, bounded planning, safe launch metadata, Developer Console rendering, and Prometheus/Grafana-compatible observability rules.

Out of scope and not implemented:

- homelab-specific templates
- NetBird, OCI, Argo CD, nmem, Kubernetes, Argo Workflows, or external executors
- desktop app, bridge, or multi-agent task graph runtime
- real external LLM calls, hosted services, Docker-only tests, or production credentials
- direct command/tool execution from templates

## Landed Goals

- G93: mapped current Topic, Schedule, Workspace, Action Safety, Context, Observability, and Console state.
- G94: accepted ADR-0041 defining Bee workflow/task boundaries.
- G95: added task manifest parser and recursive sanitizer.
- G96: added durable Bee task/node records and `PGBeeTaskStore`.
- G97: added safe topic-bound Bee lifecycle anchors.
- G98: added deterministic bounded planner and atomic ready-node claiming.
- G99: added safe launch metadata for normal durable run creation.
- G100: added Developer Console Bee page and low-cardinality Bee Prometheus labels.
- G101: added final smoke test, usage docs, and this implementation report.

## Key Boundaries

- AgentKit Core remains generic.
- Bee does not introduce a parallel runtime executor.
- Bee nodes are planned into launch intents and later normal durable runs.
- Existing HITL, approval, command, workspace, path, validation, and action-safety policies remain authoritative.
- Task, node, topic, run, and session IDs are allowed in durable records, console routes/pages, and safe correlation metadata, but not Prometheus labels.
- Raw prompt/content/message/result/secret/text/command output/stdout/stderr/env is rejected or omitted from manifests, metadata, console pages, traces, and metrics.

## Verification Evidence

Focused verification run during G101:

- `uv run pytest tests/coding_agent/test_bee_runtime.py -v` -> 53 passed

The final smoke test is:

- `test_bee_runtime_smoke_manifest_topic_launch_console_metrics`

It covers:

- safe manifest parsing
- durable Bee task/node records
- Bee task lifecycle anchors
- bounded planner launch intent
- safe launch metadata
- Developer Console Bee rendering
- Prometheus low-cardinality labels without task/node/topic/run/session IDs

Additional phase verification is recorded in `docs/bee_runtime/GOAL_PROGRESS.md`.

## Remaining Risks

- Bee launch metadata is not yet wired to automatically create durable runs. That must be done through existing session/runtime paths in a future phase.
- No external executor adapter exists by design.
- No homelab-specific templates exist by design.
- The legacy file-overlap `TopicPlugin` has been removed; Bee anchors continue to avoid raw prompt/message/content/command output and use safe product metadata.
