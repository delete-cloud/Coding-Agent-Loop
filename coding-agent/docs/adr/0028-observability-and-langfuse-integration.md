# ADR-0028: Add layered observability with optional Langfuse export

**Status**: Proposed
**Date**: 2026-05-16
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0024 through ADR-0027 made remote runs result-first, durable enough to
retain sessions and workspaces, and partially moved provider-neutral result
models into `agentkit`. The next product gap is operational visibility.

Today the system has several useful but disconnected signals:

- standard Python logs in `agentkit` and `coding_agent`;
- `agentkit.tracing`, which currently wraps `structlog` and can emit JSON
  events when enabled;
- in-memory `SessionMetricsPlugin` data in `coding_agent`;
- session, workspace, result, and publication metadata stored in the
  `coding_agent` control plane.

These are not enough for dogfood or production operation. Operators need to
answer questions such as:

- Which turn failed, and in which pipeline stage?
- How long did context building, LLM generation, tool execution, approval wait,
  workspace provisioning, and publication take?
- Which provider/model was used, with what token usage and error outcome?
- Did exporter failures affect agent execution?
- Did any trace contain prompt text, file content, shell output, secrets, or
  high-cardinality unsafe labels?
- Can traces be grouped by session id and correlated with workspace/result
  metadata?

Langfuse is a good fit for LLM and agent trace review, but it should not become
part of the core runtime contract. Langfuse documentation currently presents an
OpenTelemetry-native approach and supports OTLP ingestion over HTTP at
`/api/public/otel`. This repository should therefore treat Langfuse as an
optional exporter behind a `coding_agent` adapter, not as a dependency that
leaks into `agentkit` business logic.

## Decision

Introduce a layered observability model:

- `agentkit` owns vendor-neutral observation primitives: span records,
  observation events, a no-op sink, and a small context-manager helper such as
  `record_span(...)`.
- `agentkit` instrumentation emits provider-neutral spans for runtime concepts:
  pipeline stages, LLM generation, tool calls, and approval waits.
- `coding_agent` maps product-specific concepts into spans: sessions, remote
  workspaces, setup/agent phases, diff/patch/archive, branch/PR publication,
  retention, storage operations, and HTTP request context.
- Langfuse/OTLP is implemented only as a `coding_agent` exporter adapter.
  Business code must not import Langfuse directly.

The default behavior must remain no-op and dependency-free. Observability should
be enabled by configuration and environment variables. Exporter failures must
fail open: they may be logged, but they must not fail agent turns, HTTP
requests, workspace cleanup, or process shutdown.

### Observability Surface

P0 spans should cover the local runtime path:

- `pipeline.stage` with stage name, status, duration, session id, and turn id;
- `llm.generation` with provider, model, status, duration, token usage when
  available, and error class/message summary on failure;
- `tool.call` with tool name, call id, status, duration, approval outcome, and
  sanitized argument summary;
- `approval.wait` with request id, tool name, status, duration, and timeout
  outcome.

P1/P2 spans should cover `coding_agent` product behavior:

- `workspace.provision`, `workspace.snapshot_import`, `workspace.setup_phase`,
  `workspace.agent_phase`, `workspace.cleanup`, and `workspace.gc`;
- `result.diff`, `result.patch`, `result.archive`, `result.publish_branch`, and
  `result.publish_pr`;
- `storage.session`, `storage.workspace`, and `session.owner_lease` for
  persistent store operations and owner fencing.

Span records should use stable, low-cardinality names and attributes. Dynamic
values such as session id, turn id, workspace id, provider instance id, and PR
URL are attributes, not span names.

### Trace Shape

Each agent turn should create or join one logical trace. The preferred grouping
model is:

```text
trace: coding_agent.turn
  span: pipeline.stage(resolve_session)
  span: pipeline.stage(load_state)
  span: pipeline.stage(build_context)
  span: llm.generation
  span: tool.call(file_read)
  span: approval.wait
  span: tool.call(shell_command)
  span: pipeline.stage(save_state)
  span: pipeline.stage(render)
  span: pipeline.stage(dispatch)
```

For remote runs, product spans may appear before or after the turn:

```text
trace: coding_agent.remote_session
  span: workspace.provision
  span: workspace.snapshot_import
  span: workspace.setup_phase
  span: workspace.agent_phase
  trace/span: coding_agent.turn
  span: result.diff
  span: result.publish_branch
  span: result.publish_pr
  span: workspace.cleanup
```

Implementations may use one trace per turn with `session_id` grouping, or a
session root trace with turn child spans, as long as Langfuse can group by
session id and an operator can inspect one turn without loading an unbounded
session history.

### Redaction And Data Safety

Safety is a first-class part of this ADR.

Defaults:

- `capture_inputs = "metadata"`;
- `capture_tool_outputs = false`;
- prompt text, file content, shell output, environment values, and secrets are
  not exported by default;
- owner/user identifiers use scoped labels or hashes by default;
- redaction failure drops the unsafe attribute instead of exporting raw content;
- allowlisted attributes should be small and low-cardinality.

The observability layer may record sanitized summaries, such as:

- tool name and call id;
- command executable or command category, not full shell script by default;
- file path if policy allows it, not file content;
- LLM provider/model and token counts;
- result publication branch/commit/PR URL;
- workspace id and provider instance id.

Any future mode that exports full prompt text, tool arguments, file content, or
shell output must be explicit, documented, and disabled in production dogfood
configuration unless the operator opts in.

### Configuration

Suggested configuration:

```toml
[observability]
enabled = false
environment = "dogfood"
service_name = "coding-agent"
sample_rate = 1.0
capture_inputs = "metadata" # metadata | redacted | full
capture_tool_outputs = false
flush_timeout_seconds = 5

[observability.langfuse]
enabled = false
endpoint = "http://127.0.0.1:3000/api/public/otel"
public_key_env = "LANGFUSE_PUBLIC_KEY"
secret_key_env = "LANGFUSE_SECRET_KEY"
```

The adapter may also honor standard OTLP environment variables where practical,
for example `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS`.

Langfuse credentials must be read from environment variables or a secret
manager. They must not be stored in repo config files.

### Langfuse Integration Boundary

The Langfuse adapter belongs in `coding_agent`, not `agentkit`.

Allowed:

- a `coding_agent` exporter that translates observation records to OTLP or
  Langfuse SDK calls;
- configuration parsing in `coding_agent`;
- server startup/shutdown lifecycle hooks that initialize and flush the
  exporter with a timeout;
- tests that use fake exporters/sinks.

Disallowed:

- `from langfuse import ...` in `agentkit`;
- Langfuse SDK objects in `PipelineContext`, `Toolset`, workspace providers, or
  result models;
- direct exporter calls spread through business logic;
- exporter failures affecting agent execution.

Prefer OTLP first. If the Langfuse SDK provides materially better support for
generation/session grouping, it may be used inside the adapter only.

## Non-goals

- Do not replace Prometheus, host metrics, container metrics, or infrastructure
  monitoring.
- Do not build a billing system or quota dashboard in this ADR.
- Do not implement full trace search, dashboards, or scoring workflows in the
  first implementation.
- Do not export prompt text, file content, shell output, secrets, or full env by
  default.
- Do not add PG-backed `ArtifactStore` or `ResultStore` because of
  observability.
- Do not move remote workspace retention, Docker cleanup, Git publication, or
  HTTP schemas into `agentkit`.
- Do not require Langfuse to run local tests, CLI usage, or the HTTP server.
- Do not make observability a hard dependency for successful agent execution.

## Alternatives Rejected

- **Keep only logs and in-memory metrics**. Rejected because logs do not provide
  enough structured parent/child timing, LLM generation context, or session-level
  trace review for remote dogfood.
- **Import Langfuse directly in runtime code**. Rejected because it would couple
  `agentkit` and business logic to one vendor and make tests/deployments depend
  on exporter behavior.
- **Use only OpenTelemetry auto-instrumentation**. Rejected because the highest
  value spans are domain-specific: pipeline stages, tool calls, approvals,
  workspace phases, and publication outcomes.
- **Capture full prompts and tool payloads by default**. Rejected because remote
  coding agents routinely handle private code, shell output, env values, package
  tokens, and other sensitive data.
- **Implement remote workspace/product spans first**. Rejected because it would
  pull product-specific metadata into the abstraction before the no-op core and
  local runtime instrumentation are stable.

## Consequences

- `agentkit` gets a reusable observation boundary without adopting Langfuse or
  OpenTelemetry as a core dependency in P0.
- `coding_agent` can support Langfuse dogfood without spreading vendor code
  through runtime, tool, or workspace modules.
- The first implementation can be tested entirely with a fake sink.
- Observability will initially be incomplete; remote workspace and publication
  spans intentionally come after local pipeline/tool/LLM spans.
- Redaction and attribute cardinality become part of review criteria for any
  future observability PR.

## Implementation Plan

### PR 1: Add ADR only

- Add this ADR.
- Do not implement code.

### PR 2: Add minimal `agentkit` observability core

Affected paths:

- `src/agentkit/observability/`
- `tests/agentkit/observability/`

Add:

- `ObservationEvent`
- `SpanRecord`
- `ObservationSink` protocol
- `NoopObservationSink`
- context-manager helper such as `record_span(...)`

Constraints:

- no external dependencies;
- default no-op behavior;
- explicit attributes map;
- exception status is recorded and re-raised;
- tests use an in-memory sink.

### PR 3: Instrument local runtime path

Affected paths:

- `src/agentkit/runtime/pipeline.py`
- `src/agentkit/tools/toolset.py`
- provider event handling paths as needed
- `tests/agentkit/runtime/`
- `tests/agentkit/tools/`

Order:

1. pipeline stage spans;
2. LLM generation spans with token usage when available;
3. tool call spans with sanitized argument summaries;
4. approval wait spans.

Keep the existing `agentkit.tracing` behavior compatible until it is explicitly
deprecated or folded into the new sink.

### PR 4: Add `coding_agent` observability config and lifecycle

Affected paths:

- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/core/config.py` or existing config loader locations;
- dogfood config templates, if present;
- focused HTTP/config tests.

Add:

- `[observability]` config parsing;
- environment variable handling;
- exporter startup/shutdown lifecycle;
- flush timeout;
- fail-open behavior on exporter errors.

### PR 5: Add Langfuse/OTLP adapter

Affected paths:

- `src/coding_agent/observability/`
- tests using fake exporter clients;
- optional dependency configuration if a SDK is selected.

Implement:

- OTLP/Langfuse exporter behind an adapter;
- Langfuse session grouping attributes;
- generation span mapping for provider/model/token usage;
- redaction enforcement before export.

Do not import Langfuse outside the adapter.

### PR 6: Add remote workspace, publication, and storage spans

Affected paths:

- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_store.py`
- `src/coding_agent/ui/workspace_store.py`
- focused workspace/publication/storage tests.

Cover:

- workspace provision/import/setup/agent/cleanup/GC;
- diff/patch/archive;
- branch publish, PR publish, and partial failure;
- retention, lost workspace, cleanup failed;
- PG session/workspace store latency and errors;
- owner lease acquire/renew/release conflict outcomes.

### PR 7: Dogfood and documentation

Add dogfood docs/config examples proving:

- traces group by `session_id`;
- one turn shows pipeline/tool/LLM lifecycle;
- remote publication/workspace spans are visible after P2;
- redaction defaults do not upload prompt text, file content, shell output, or
  secrets;
- disabling the exporter or stopping Langfuse does not break agent execution.

## Acceptance Criteria

- [ ] ADR exists at
  `docs/adr/0028-observability-and-langfuse-integration.md`.
- [ ] ADR states the `agentkit` / `coding_agent` / Langfuse layering boundary.
- [ ] ADR states redaction defaults and fail-open exporter behavior.
- [ ] ADR splits implementation into core, instrumentation, exporter, remote
  spans, and dogfood phases.
- [ ] P0 implementation has no external dependency and is no-op by default.
- [ ] Pipeline/tool/LLM instrumentation tests verify spans through a fake sink.
- [ ] Exporter tests verify failures do not fail agent turns or server shutdown.
- [ ] Dogfood trace verifies session grouping and default redaction.

## References

- `docs/adr/0024-remote-result-publication.md`
- `docs/adr/0025-durable-remote-session-and-workspace-retention.md`
- `docs/adr/0026-remote-result-publication-v2-git-review-flow.md`
- `docs/adr/0027-agentkit-session-result-and-artifact-references.md`
- `src/agentkit/tracing.py`
- `src/agentkit/runtime/pipeline.py`
- `src/agentkit/tools/toolset.py`
- `src/coding_agent/plugins/metrics.py`
- Langfuse OpenTelemetry integration:
  `https://langfuse.com/integrations/native/opentelemetry`
- Langfuse Python SDK overview:
  `https://langfuse.com/docs/observability/sdk/overview`
