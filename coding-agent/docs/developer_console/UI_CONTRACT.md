# Developer Console UI Contract

Date: 2026-05-20

## Scope

The Developer Console is a Coding Agent debug/product surface over existing
durable runtime, context, memory, action, observability, and release data. It
does not define new AgentKit Core behavior.

## Route Contract

Required phase routes:

- `/console`
- `/console/sessions`
- `/console/runs`
- `/console/runs/{run_id}`
- `/console/interactions`
- `/console/tape`
- `/console/context`
- `/console/memory`
- `/console/actions`
- `/console/observability`
- `/console/release`

Routes may render empty states when backing APIs are not configured. Empty
states must be explicit and must not raise internal errors for normal local
development without PostgreSQL, Langfuse, Grafana, or Prometheus enabled.

## Navigation Contract

Every console page must include links for:

- Sessions
- Runs
- HITL / Interactions
- Tape
- Context
- Memory
- Actions / Validation
- Observability
- Release / Health

## Data Contract

Console pages may render:

- ids: `session_id`, `run_id`, `tape_id`, retrieval/action/validation ids,
  `interaction_id`
- status values and bounded enum values
- timestamps and durations
- safe error summaries
- safe evidence metadata: source kind, source path, line range, reason, score,
  confidence
- safe action metadata: action kind, policy decision, risk level, validation
  status, changed path counts, extension buckets
- health/readiness status
- release verification command ids and commands from the local manifest
- safe local links to `/metrics`, Grafana, or Langfuse when configured

Console pages must not render:

- secrets, API keys, authorization headers, tokens, passwords
- raw prompts, messages, model results, or full tool payloads
- raw file content or patch content
- raw command output, stdout, stderr, or environment values
- raw retrieved document bodies unless an existing sanitized context contract
  explicitly allows them for display
- high-cardinality ids as Prometheus labels

## Action Contract

The console is read-only by default. If a later goal exposes approval
resolution, it must call the existing approval endpoint and preserve existing
approval/action policy behavior. The console must not execute commands, apply
patches, restore checkpoints, or publish branches through new bypass routes.

## Rendering Contract

- HTML must be escaped by default.
- Missing optional data must render an empty state.
- Trace/debug metadata must be compact and scannable.
- Page rendering must not require external services.
- Page rendering must not add raw sensitive data to tracing attributes.

## Testing Contract

Each page must have deterministic route/render tests. At minimum, tests must
assert:

- the route returns HTML successfully with empty or fixture data
- navigation links are present
- expected safe fields render
- forbidden sensitive strings do not render
- existing smoke tests for durable runtime, context system, action safety,
  observability, and release verification remain valid where relevant
