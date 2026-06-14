from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


CHART_DIR = Path(__file__).resolve().parents[2] / "helm"


def _render_chart(*extra_args: str) -> list[dict]:
    command = [
        "helm",
        "template",
        "coding-agent-test",
        str(CHART_DIR),
        *extra_args,
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        doc for doc in yaml.safe_load_all(completed.stdout) if isinstance(doc, dict)
    ]


def _deployment_doc(docs: list[dict]) -> dict:
    return next(doc for doc in docs if doc.get("kind") == "Deployment")


def test_chart_uses_healthz_and_readyz_probes() -> None:
    docs = _render_chart()
    deployment = _deployment_doc(docs)
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"


def test_chart_renders_secret_ref_and_secret_env() -> None:
    docs = _render_chart(
        "--set",
        "agent.secretRef[0]=shared-agent-secret",
        "--set",
        "agent.secretEnv[0].name=AGENT_API_KEY",
        "--set",
        "agent.secretEnv[0].secretName=provider-secret",
        "--set",
        "agent.secretEnv[0].secretKey=api-key",
    )
    deployment = _deployment_doc(docs)
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert container["envFrom"] == [{"secretRef": {"name": "shared-agent-secret"}}]
    assert {
        "name": "AGENT_API_KEY",
        "valueFrom": {"secretKeyRef": {"name": "provider-secret", "key": "api-key"}},
    } in container["env"]
