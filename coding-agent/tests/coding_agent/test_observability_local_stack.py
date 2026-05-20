from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


LOCAL_STACK = Path("docs/observability/local")
PROMETHEUS_CONFIG = LOCAL_STACK / "prometheus.yml"
ALERT_RULES = LOCAL_STACK / "alert-rules.yml"
GRAFANA_DATASOURCE = LOCAL_STACK / "grafana/provisioning/datasources/prometheus.yml"
GRAFANA_DASHBOARD_PROVIDER = (
    LOCAL_STACK / "grafana/provisioning/dashboards/coding-agent.yml"
)
GRAFANA_DASHBOARD = LOCAL_STACK / "grafana/dashboards/coding-agent-observability.json"
LOCAL_STACK_DOC = Path("docs/observability/LOCAL_STACK.md")

FORBIDDEN_PROMETHEUS_LABELS = {
    "run_id",
    "session_id",
    "trace_id",
    "event_id",
    "interaction_id",
    "tool_call_id",
    "file_path",
    "prompt",
    "message",
    "content",
    "command_output",
    "secret",
}
FORBIDDEN_SECRET_KEYS = {
    "authorization",
    "bearer",
    "password",
    "secret_key",
    "token",
}
REQUIRED_DASHBOARD_METRICS = {
    "coding_agent_http_requests_total",
    "coding_agent_http_request_duration_ms",
    "coding_agent_observation_spans_total",
    "coding_agent_observation_span_duration_ms",
    "coding_agent_observation_events_total",
    "coding_agent_evaluation_case_results_total",
    "coding_agent_hitl_interactions_total",
    "coding_agent_storage_operations_total",
    "coding_agent_storage_operation_duration_ms",
}


def test_observability_local_stack_files_exist_and_parse() -> None:
    for path in (
        PROMETHEUS_CONFIG,
        ALERT_RULES,
        GRAFANA_DATASOURCE,
        GRAFANA_DASHBOARD_PROVIDER,
        GRAFANA_DASHBOARD,
        LOCAL_STACK_DOC,
    ):
        assert path.is_file(), path

    assert isinstance(_read_yaml(PROMETHEUS_CONFIG), dict)
    assert isinstance(_read_yaml(ALERT_RULES), dict)
    assert isinstance(_read_yaml(GRAFANA_DATASOURCE), dict)
    assert isinstance(_read_yaml(GRAFANA_DASHBOARD_PROVIDER), dict)
    assert isinstance(_read_json(GRAFANA_DASHBOARD), dict)


def test_prometheus_scrapes_only_local_coding_agent_metrics_endpoint() -> None:
    config = _read_yaml(PROMETHEUS_CONFIG)
    scrape_configs = config["scrape_configs"]
    assert len(scrape_configs) == 1

    scrape = scrape_configs[0]
    assert scrape["job_name"] == "coding-agent"
    assert scrape["metrics_path"] == "/metrics"
    assert scrape["static_configs"] == [
        {
            "targets": ["host.docker.internal:8080"],
            "labels": {"service": "coding-agent", "environment": "local"},
        }
    ]


def test_grafana_provisions_local_prometheus_datasource_and_dashboard() -> None:
    datasource = _read_yaml(GRAFANA_DATASOURCE)
    datasources = datasource["datasources"]
    assert datasources == [
        {
            "name": "Prometheus",
            "uid": "prometheus",
            "type": "prometheus",
            "access": "proxy",
            "url": "http://prometheus:9090",
            "isDefault": True,
            "editable": True,
        }
    ]

    provider = _read_yaml(GRAFANA_DASHBOARD_PROVIDER)
    provider_config = provider["providers"][0]
    assert provider_config["type"] == "file"
    assert provider_config["options"]["path"] == (
        "/etc/grafana/provisioning/dashboards/json"
    )

    dashboard = _read_json(GRAFANA_DASHBOARD)
    assert dashboard["uid"] == "coding-agent-observability"
    panel_exprs = _dashboard_target_exprs(dashboard)
    for metric in REQUIRED_DASHBOARD_METRICS:
        assert any(metric in expr for expr in panel_exprs), metric


def test_local_alerts_reference_low_cardinality_metrics() -> None:
    alerts = _read_yaml(ALERT_RULES)
    rules = alerts["groups"][0]["rules"]
    alert_names = {rule["alert"] for rule in rules}
    assert alert_names == {
        "CodingAgentMetricsEndpointDown",
        "CodingAgentHttpErrorRateHigh",
        "CodingAgentObservationErrorSpansPresent",
        "CodingAgentStorageErrorsPresent",
    }

    expressions = "\n".join(rule["expr"] for rule in rules)
    for metric in (
        "coding_agent_http_requests_total",
        "coding_agent_observation_spans_total",
        "coding_agent_storage_operations_total",
    ):
        assert metric in expressions
    for label in FORBIDDEN_PROMETHEUS_LABELS:
        assert label not in expressions


def test_observability_local_stack_files_have_no_production_secrets() -> None:
    config_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PROMETHEUS_CONFIG,
            ALERT_RULES,
            GRAFANA_DATASOURCE,
            GRAFANA_DASHBOARD_PROVIDER,
            GRAFANA_DASHBOARD,
        )
    ).casefold()
    doc_text = LOCAL_STACK_DOC.read_text(encoding="utf-8").casefold()

    for forbidden in FORBIDDEN_SECRET_KEYS:
        assert forbidden not in config_text
    for label in FORBIDDEN_PROMETHEUS_LABELS:
        assert label not in config_text
        assert label in doc_text


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dashboard_target_exprs(dashboard: dict[str, Any]) -> list[str]:
    exprs: list[str] = []
    for panel in dashboard["panels"]:
        for target in panel["targets"]:
            exprs.append(target["expr"])
    return exprs
