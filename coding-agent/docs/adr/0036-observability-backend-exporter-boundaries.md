# ADR-0036: Observability backend exporter boundaries

**Status**: Accepted
**Date**: 2026-05-20

## Context

ADR-0028 introduced provider-neutral observation primitives in AgentKit and an
optional Coding Agent Langfuse/OTLP exporter. The next observability-platform
phase adds service-level Prometheus metrics and local Grafana dashboards while
preserving Langfuse/OTLP tracing.

Those systems solve different problems. Langfuse/OTLP is a tracing exporter for
span inspection and trace correlation. Prometheus is a metrics exporter for
service-level counters, histograms, and gauges. Grafana is dashboard and
provisioning configuration. Treating all three as one provider type would blur
runtime boundaries and could make tracing and metrics mutually exclusive.

## Decision

Keep AgentKit Core provider-neutral. AgentKit may emit `SpanRecord` and
`ObservationEvent` values through `ObservationSink`, but it must not import
Langfuse, Prometheus, Grafana, or Coding Agent exporter implementations.

Coding Agent owns observability backend factory and wiring:

- Langfuse/OTLP remains a tracing backend/exporter.
- Prometheus is a metrics backend/exporter.
- Grafana is local dashboard/provisioning configuration and is not a Python
  runtime dependency.
- `CompositeObservationSink` fans out observations so tracing and metrics can
  be enabled together.

Prometheus metrics must use stable, low-cardinality names and labels. The
metrics exporter must reject, drop, or normalize forbidden labels rather than
export high-cardinality or sensitive labels.

Forbidden Prometheus labels include:

- `run_id`
- `session_id`
- `trace_id`
- `event_id`
- `interaction_id`
- `tool_call_id`
- `file_path`
- `prompt`
- `message`
- `content`
- `command_output`
- `secret`

Tracing and metrics must not export raw prompt text, file content, shell
output, environment values, secrets, full tool payloads, command output,
message content, model results, or other unredacted text.

Exporter failures must fail open. Langfuse, OTLP, Prometheus, and composite
sink failures must not break agent turns, HTTP requests, runtime cleanup,
storage operations, or process shutdown.

## Alternatives Rejected

- **Make Prometheus another value of `observability.backend` that replaces
  Langfuse/OTLP**. Rejected because metrics and tracing should be additive, not
  mutually exclusive.
- **Put Prometheus registry/exporter logic in AgentKit**. Rejected because
  AgentKit should remain a provider-neutral runtime/framework layer.
- **Use Grafana as a Python runtime dependency**. Rejected because Grafana
  belongs in local provisioning/dashboard artifacts for this phase.
- **Use run/session/trace ids as Prometheus labels for easier debugging**.
  Rejected because those labels are high-cardinality and can make Prometheus
  unusable.
- **Export raw prompts, file contents, shell output, or tool payloads for richer
  dashboards**. Rejected because this repository handles private code,
  environment values, shell output, and secrets.

## Acceptance Criteria

- [ ] `test_composite_observation_sink_records_spans_and_events_to_all_sinks`
- [ ] `test_composite_observation_sink_fail_opens_when_child_sink_fails`
- [ ] `test_build_observation_sink_supports_tracing_only`
- [ ] `test_build_observation_sink_supports_metrics_only`
- [ ] `test_build_observation_sink_supports_tracing_and_metrics`
- [ ] `test_build_observation_sink_returns_none_when_disabled`
- [ ] `test_prometheus_metrics_drop_forbidden_high_cardinality_labels`
- [ ] `test_prometheus_metrics_fail_open_when_registry_write_fails`
- [ ] `test_metrics_endpoint_returns_prometheus_text_when_enabled`
- [ ] `test_metrics_endpoint_can_be_disabled`
- [ ] `test_metrics_endpoint_exposition_has_no_forbidden_labels_or_raw_text`
- [ ] `test_observability_local_stack_files_have_no_production_secrets`
- [ ] `uv run pytest tests/coding_agent/test_observability.py -v`
- [ ] `uv run pytest tests/coding_agent/test_release_observability_contract.py -v`
- [ ] `uv run pytest tests/ui/test_http_server.py -k "metrics" -v`
- [ ] `git diff --check -- .`

## References

- `docs/adr/0028-observability-and-langfuse-integration.md`
- `docs/observability/CURRENT_STATE.md`
- `src/agentkit/observability/`
- `src/coding_agent/observability.py`
- `src/coding_agent/ui/http_server.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_release_observability_contract.py`
