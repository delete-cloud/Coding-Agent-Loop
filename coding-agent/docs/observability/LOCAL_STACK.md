# Local Observability Stack

This stack runs local Prometheus and Grafana against the Coding Agent `/metrics`
endpoint. It is for deterministic development and smoke verification only. It
does not require hosted services, production credentials, Loki, Tempo,
Kubernetes, or an LGTM stack.

## Coding Agent Metrics

Enable the metrics endpoint in the local Coding Agent config:

```toml
[observability]
enabled = true

[observability.metrics]
enabled = true
backend = "prometheus"
endpoint_enabled = true
```

Start the app on the default scrape target:

```bash
uv run python -m coding_agent serve --host 127.0.0.1 --port 8080
```

Confirm metrics locally:

```bash
curl -fsS http://127.0.0.1:8080/metrics
```

## Prometheus And Grafana

From `docs/observability/local`, start the local stack:

```bash
docker compose up
```

Prometheus runs at `http://127.0.0.1:9090` and scrapes
`host.docker.internal:8080/metrics`. Grafana runs at
`http://127.0.0.1:3000` with anonymous local viewer access and a provisioned
Prometheus datasource.

## Files

- `docs/observability/local/prometheus.yml` defines the local scrape target.
- `docs/observability/local/alert-rules.yml` defines local Prometheus alerts.
- `docs/observability/local/grafana/provisioning/datasources/prometheus.yml`
  provisions the Prometheus datasource.
- `docs/observability/local/grafana/provisioning/dashboards/coding-agent.yml`
  provisions dashboard loading.
- `docs/observability/local/grafana/dashboards/coding-agent-observability.json`
  defines the Coding Agent dashboard.

## Safety Rules

The local stack must stay credential-free. Prometheus metrics must keep
low-cardinality labels and must not include `run_id`, `session_id`, `trace_id`,
`event_id`, `interaction_id`, `tool_call_id`, `file_path`, `prompt`,
`message`, `content`, `command_output`, result text, or `secret` values.
