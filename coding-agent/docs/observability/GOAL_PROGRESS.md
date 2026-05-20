# Observability Platform G46-G53 Goal Progress

Date started: 2026-05-20

## Phase Scope

This phase adds service-level observability through Prometheus metrics and
Grafana local dashboards while preserving existing Langfuse/OTLP tracing.

AgentKit Core remains provider-neutral and depends only on
`ObservationSink`, `SpanRecord`, and `ObservationEvent`. Coding Agent owns
backend/exporter wiring.

## Global Constraints

- Do not rewrite AgentKit Core.
- Do not change G00-G45 behavior.
- Do not remove Langfuse/OTLP support.
- Do not make Prometheus mutually exclusive with Langfuse.
- Do not introduce Loki, Tempo, Kubernetes, or a full LGTM stack.
- Do not require external hosted services, production credentials, or real LLM
  calls.
- Do not use high-cardinality Prometheus labels: `run_id`, `session_id`,
  `trace_id`, `event_id`, `interaction_id`, `tool_call_id`, `file_path`,
  `prompt`, `message`, `content`, `command_output`, or `secret`.
- Do not add raw prompt text, file content, shell output, environment values,
  secrets, full tool payloads, command output, message content, model results,
  or other unredacted text to tracing attributes or metrics.
- Metrics failures must not break the main runtime.

## Planned Goals

| Goal | Scope |
| --- | --- |
| G46 | Current-state observability map. |
| G47 | ADR for observability backend/exporter boundaries. |
| G48 | Config parsing/factory and `CompositeObservationSink`. |
| G49 | Prometheus metrics registry and sink. |
| G50 | Metrics endpoint and HTTP request metrics. |
| G51 | Representative runtime/context/action metrics. |
| G52 | Local Prometheus/Grafana stack config and alerts. |
| G53 | E2E smoke, no-leak checks, and final implementation report. |

## G46 Observability Current State Map

Status: merged via PR #252.

### Intended Files

- `docs/observability/CURRENT_STATE.md`
- `docs/observability/GOAL_PROGRESS.md`

### Verification Commands

- `test -f docs/observability/CURRENT_STATE.md`
- `test -f docs/observability/GOAL_PROGRESS.md`
- `rg -n "backend == \"noop\"|backend not in \\{\"otlp_http\", \"langfuse\"\\}|OtlpHttpObservationSink|_SENSITIVE_ATTRIBUTE_PARTS" src/coding_agent/observability.py`
- `rg -n "observation_sink|build_observation_sink" src/coding_agent/app.py src/agentkit`
- `rg -n "\"/metrics\"|prometheus|grafana" src tests docs || true`
- `git diff --check -- .`

### Stop Criteria

- Stop if current observability boundaries cannot be described without changing
  production code.
- Stop if the existing Langfuse/OTLP implementation appears to require
  replacement rather than additive extension.

### Changed Files

- `docs/observability/CURRENT_STATE.md`
- `docs/observability/GOAL_PROGRESS.md`

### Tests Run

- `test -f docs/observability/CURRENT_STATE.md`
- `test -f docs/observability/GOAL_PROGRESS.md`
- `rg -n "backend == \"noop\"|backend not in \\{\"otlp_http\", \"langfuse\"\\}|OtlpHttpObservationSink|_SENSITIVE_ATTRIBUTE_PARTS" src/coding_agent/observability.py`
- `rg -n "observation_sink|build_observation_sink" src/coding_agent/app.py src/agentkit`
- `rg -n "\"/metrics\"|prometheus|grafana" src tests docs || true`
- `git diff --check -- .`

### Results

- File existence checks passed.
- Read-only evidence confirmed current `noop`, `otlp_http`, and `langfuse`
  backend handling, OTLP sink construction, sensitive-key filtering, app-level
  observation sink wiring, and AgentKit's `ObservationSink` abstraction usage.
- Read-only evidence found no existing Prometheus/Grafana implementation and
  no existing `/metrics` HTTP endpoint.
- `git diff --check -- .` passed.

### Remaining Risks

- G46 is a documentation-only map. It does not yet add the composite sink,
  Prometheus exporter, metrics endpoint, or Grafana local stack.

## G47 Observability Backend ADR

Status: merged via PR #253.

### Intended Files

- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/observability/GOAL_PROGRESS.md`

### Verification Commands

- `test -f docs/adr/0036-observability-backend-exporter-boundaries.md`
- `rg -n "Langfuse/OTLP remains a tracing backend|Prometheus is a metrics backend|CompositeObservationSink|Forbidden Prometheus labels|Exporter failures must fail open" docs/adr/0036-observability-backend-exporter-boundaries.md`
- `git diff --check -- .`
- `git diff --cached --check -- .`

### Stop Criteria

- Stop if the backend/exporter decision requires AgentKit to import Langfuse,
  Prometheus, Grafana, or Coding Agent runtime modules.
- Stop if Prometheus metrics need high-cardinality labels to satisfy the phase.

### Changed Files

- `docs/adr/0036-observability-backend-exporter-boundaries.md`
- `docs/observability/GOAL_PROGRESS.md`

### Tests Run

- `test -f docs/adr/0036-observability-backend-exporter-boundaries.md`
- `rg -n "Langfuse/OTLP remains a tracing backend|Prometheus is a metrics backend|CompositeObservationSink|Forbidden Prometheus labels|Exporter failures must fail open" docs/adr/0036-observability-backend-exporter-boundaries.md`
- `git diff --check -- .`
- `git diff --cached --check -- .`

### Results

- ADR file exists.
- Key backend/exporter boundary decisions are present.
- `git diff --check -- .` passed.
- `git diff --cached --check -- .` passed.

### Remaining Risks

- G47 is decision documentation only. The composite sink, Prometheus exporter,
  metrics endpoint, and dashboard artifacts remain for G48-G53.

## G48 Observability Factory And Composite Sink

Status: passed local verification; pending PR.

### Intended Files

- `src/coding_agent/observability.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_bootstrap.py`
- `docs/observability/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run pytest tests/coding_agent/test_bootstrap.py -k "observation_sink or observability" -v`
- `uv run pytest tests/coding_agent/test_release_observability_contract.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/observability.py tests/coding_agent/test_observability.py tests/coding_agent/test_bootstrap.py`
- `uv run ruff check src/coding_agent/observability.py tests/coding_agent/test_observability.py tests/coding_agent/test_bootstrap.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if additive tracing plus metrics wiring requires AgentKit pipeline
  changes.
- Stop if Prometheus metrics require high-cardinality labels for the factory
  contract.
- Stop if preserving legacy flat Langfuse/OTLP config conflicts with nested
  tracing/metrics config.

### Changed Files

- `src/coding_agent/observability.py`
- `tests/coding_agent/test_observability.py`
- `tests/coding_agent/test_bootstrap.py`
- `docs/observability/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run pytest tests/coding_agent/test_bootstrap.py -k "observation_sink or observability" -v`
- `uv run pytest tests/coding_agent/test_release_observability_contract.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/observability.py tests/coding_agent/test_observability.py tests/coding_agent/test_bootstrap.py`
- `uv run ruff check src/coding_agent/observability.py tests/coding_agent/test_observability.py tests/coding_agent/test_bootstrap.py`
- `git diff --check -- .`

### Results

- Observability unit tests passed: 15 passed.
- Bootstrap observability tests passed: 4 passed, 18 deselected.
- Release observability contract tests passed: 3 passed.
- AgentKit runtime span regression passed: 1 passed, 36 deselected.
- Scoped ruff format/check passed.
- `git diff --check -- .` passed.
- Local review found that nested metrics config could suppress existing flat
  Langfuse/OTLP tracing config. The factory now preserves flat tracing config
  when `[observability.metrics]` is added without `[observability.tracing]`, and
  a regression test covers that additive path.

### Remaining Risks

- G48 adds the additive factory/composite shape and a metrics exporter type.
  The real Prometheus registry, metric mapping, label normalization, and text
  exposition are still G49-G50 work.
