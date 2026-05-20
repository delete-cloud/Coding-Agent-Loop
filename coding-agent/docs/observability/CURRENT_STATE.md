# Observability Platform Current State

Date: 2026-05-20

## Scope

This map covers the repository state before G46-G53 observability-platform work.
The phase is about observability backends and exporters. It is not an LLM
provider phase, and it does not include Developer Console, Debug UI, Loki,
Tempo, Kubernetes, or a full LGTM stack.

## Existing Architecture

`agentkit.observability` is provider-neutral. It defines:

- `ObservationSink`
- `SpanRecord`
- `ObservationEvent`
- `NoopObservationSink`
- `record_span(...)`

AgentKit runtime/tool instrumentation depends on those abstractions and does
not import Coding Agent exporters or Langfuse-specific code.

`coding_agent.observability` owns product-level exporter construction. It
currently supports:

- `backend = "noop"`
- `backend = "otlp_http"`
- `backend = "langfuse"`

The Langfuse path is an OTLP/HTTP exporter with Langfuse Basic Auth headers.
Langfuse is therefore a tracing backend/exporter, not an LLM provider.

## Current Wiring

`coding_agent.app.create_child_pipeline(...)` reads `cfg.extra["observability"]`,
calls `build_observation_sink(...)`, and injects the resulting sink into
`ctx.config["observation_sink"]` when configured.

The AgentKit pipeline, AgentKit toolset, KB plugin, and action-safety helpers
record spans/events through `ObservationSink` only. Exporter failures are
designed to fail open and not break runtime behavior.

## Existing Safety Contracts

The OTLP exporter drops span attributes whose keys include sensitive parts:

- `content`
- `message`
- `prompt`
- `result`
- `secret`
- `text`

Release-hardening G43 added representative safety coverage in
`tests/coding_agent/test_release_observability_contract.py`.

Existing deterministic observability tests include:

- `tests/agentkit/observability/test_core.py`
- `tests/agentkit/runtime/test_pipeline.py`
- `tests/agentkit/tools/test_toolset.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/action_safety/test_action_observability.py`
- `tests/coding_agent/plugins/test_kb_plugin.py`
- `tests/integration/test_durable_runtime_smoke.py`

## HTTP Surface

`src/coding_agent/ui/http_server.py` is a FastAPI server. It currently exposes
health/readiness style endpoints such as `/healthz`; it does not expose a
Prometheus `/metrics` endpoint.

Existing health tests live primarily under `tests/ui/test_http_server.py` and
`tests/ui/test_security.py`.

## Release Verification

`docs/release_hardening/release-verification.yaml` lists deterministic
regression gates for durable runtime, context system, action safety, evaluation,
and AgentKit pipeline context/span behavior. It does not yet include
observability-platform gates.

## Gaps For G46-G53

- No composite sink exists, so tracing and metrics cannot both be enabled
  through a single observation sink.
- No Prometheus metrics recorder/exporter exists.
- No low-cardinality Prometheus label contract exists.
- No `/metrics` HTTP endpoint exists.
- No local Prometheus/Grafana config, dashboard provisioning, or alert rules
  exist for this phase.
- No observability-platform implementation report exists.

## Constraints To Preserve

- Keep AgentKit Core provider-neutral.
- Keep Langfuse/OTLP tracing support.
- Make Prometheus additive, not mutually exclusive with Langfuse/OTLP.
- Do not export raw prompt text, file content, shell output, environment
  values, secrets, full tool payloads, command output, message content, model
  results, or other unredacted text in trace attributes or metrics.
- Do not use high-cardinality Prometheus labels such as run/session/trace/event
  ids, file paths, tool call ids, prompts, messages, contents, command output,
  or secrets.
- Metrics/exporter failures must fail open and must not break the main runtime.
