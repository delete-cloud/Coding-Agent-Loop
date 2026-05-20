# Observability Platform Implementation Report

Date completed: 2026-05-20

## Summary

G46-G53 added the Coding Agent observability platform as additive service-level
observability. Langfuse/OTLP tracing remains a tracing exporter. Prometheus is
implemented as a metrics exporter. Grafana is local dashboard/provisioning
configuration only. AgentKit Core remains provider-neutral and continues to
depend only on `ObservationSink`, `SpanRecord`, and `ObservationEvent`.

## Landed Goals

| Goal | PR | Result |
| --- | --- | --- |
| G46 | #252 | Current-state observability map. |
| G47 | #253 | ADR-0036 backend/exporter boundaries. |
| G48 | #254 | Additive factory and `CompositeObservationSink`. |
| G49 | #255 | Prometheus metrics recorder and sink. |
| G50 | #256 | `/metrics` endpoint and HTTP request metrics. |
| G51 | #257 | Runtime, context, action, eval, HITL, and storage metrics. |
| G52 | #258 | Local Prometheus/Grafana stack, alerts, and docs. |
| G53 | #259 | E2E smoke, no-leak checks, and final report. |

## Acceptance Audit

- Langfuse/OTLP support is preserved and remains additive with Prometheus
  metrics through `CompositeObservationSink`.
- Prometheus metrics use deterministic local recorder tests and low-cardinality
  labels.
- Metrics failures fail open and do not break the main runtime or HTTP
  requests.
- The `/metrics` endpoint can be enabled or disabled through local config.
- HTTP request, observation span/event, evaluation, HITL, storage, runtime,
  context, retrieval, and action metrics have representative tests.
- Local Prometheus and Grafana configuration has no production credentials and
  does not introduce Loki, Tempo, Kubernetes, or a full LGTM stack.
- No raw prompt, message, content, command output, result text, secret, or
  forbidden high-cardinality label is expected in Prometheus metrics.

## Key Artifacts

- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/observability/CURRENT_STATE.md`
- `docs/observability/GOAL_PROGRESS.md`
- `docs/observability/LOCAL_STACK.md`
- `docs/observability/local/`
- `src/coding_agent/observability.py`
- `src/coding_agent/ui/http_server.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_observability_local_stack.py`
- `tests/coding_agent/test_observability_platform_smoke.py`
- `tests/ui/test_http_server.py`

## Verification

Final G53 verification is recorded in `docs/observability/GOAL_PROGRESS.md`.
The phase used deterministic unit, smoke, HTTP, and documentation/config tests;
it did not require external hosted services, production credentials, or real
LLM calls.

## Remaining Risks

- The local Prometheus/Grafana stack is configuration-only in this phase.
  Container startup is documented but not required for deterministic test
  verification.
- G51 provides representative metrics for storage and evaluation paths rather
  than exhaustive call-path instrumentation.
