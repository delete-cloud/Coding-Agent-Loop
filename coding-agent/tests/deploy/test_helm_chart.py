from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "helm"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm:
        return helm
    if os.getenv("CI"):
        raise AssertionError("helm must be installed in CI")
    pytest.skip("helm is not installed")


def _render(*args: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [_helm(), "template", "coding-agent", str(CHART), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _object_named(
    docs: list[dict[str, Any]], kind: str, name_suffix: str
) -> dict[str, Any]:
    for doc in docs:
        name = doc.get("metadata", {}).get("name", "")
        if doc.get("kind") == kind and name.endswith(name_suffix):
            return doc
    raise AssertionError(f"missing {kind} ending with {name_suffix}")


def _deployment(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return _object_named(docs, "Deployment", "coding-agent")


def _container(
    docs: list[dict[str, Any]], name: str = "coding-agent"
) -> dict[str, Any]:
    containers = _deployment(docs)["spec"]["template"]["spec"]["containers"]
    for item in containers:
        if item["name"] == name:
            return item
    raise AssertionError(f"missing container {name}")


def _service(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return _object_named(docs, "Service", "coding-agent")


def _agent_toml(docs: list[dict[str, Any]]) -> dict[str, Any]:
    config = _object_named(docs, "ConfigMap", "-agent-config")
    return tomllib.loads(config["data"]["agent.toml"])


def _assert_storage(docs: list[dict[str, Any]], data_mount: str) -> None:
    storage = _agent_toml(docs)["storage"]
    expected = {
        "tape_backend": "sqlite",
        "http_session_backend": "sqlite",
        "checkpoint_backend": "sqlite",
        "runtime_backend": "sqlite",
    }
    for key, value in expected.items():
        actual = storage.get(key)
        assert actual == value, f"{key}={actual!r}"
    assert storage["paths"]["local"] == f"{data_mount}/local.sqlite3"
    assert storage["paths"]["docs"] == f"{data_mount}/docs"


def test_helm_chart_lints() -> None:
    subprocess.run([_helm(), "lint", str(CHART)], check=True)


@pytest.mark.parametrize(
    "values_file",
    [
        None,
        "values-orbstack.yaml",
        "values-orbstack-kimi.yaml",
    ],
)
def test_helm_values_render(values_file: str | None) -> None:
    args = []
    if values_file is not None:
        args = ["-f", str(CHART / values_file)]
    docs = _render(*args)
    assert _object_named(docs, "Deployment", "coding-agent")["apiVersion"] == "apps/v1"
    assert _object_named(docs, "Service", "coding-agent")["apiVersion"] == "v1"
    assert _object_named(docs, "ConfigMap", "-agent-config")["apiVersion"] == "v1"


def test_helm_default_runtime_contract_is_runnable() -> None:
    docs = _render()
    _assert_storage(docs, "/var/lib/coding-agent/data")
    pod_spec = _deployment(docs)["spec"]["template"]["spec"]
    assert pod_spec["securityContext"]["fsGroup"] == 10001

    main = _container(docs)
    assert main["securityContext"] == {
        "runAsGroup": 10001,
        "runAsNonRoot": True,
        "runAsUser": 10001,
    }
    assert main["ports"][0]["containerPort"] == 8080
    assert main["command"][-2:] == ["--port", "8080"]


def test_helm_service_port_override_keeps_app_port() -> None:
    docs = _render("--set", "service.port=9090")
    assert _service(docs)["spec"]["ports"][0]["port"] == 9090

    main = _container(docs)
    assert main["ports"][0]["containerPort"] == 8080
    assert main["command"][-2:] == ["--port", "8080"]


def test_helm_workspace_mount_override_sets_working_dir() -> None:
    docs = _render("--set", "persistence.workspace.mountPath=/mnt/workspace")
    assert _container(docs)["workingDir"] == "/mnt/workspace"


def test_helm_data_mount_override_updates_sqlite_bundle_path() -> None:
    docs = _render("--set", "persistence.data.mountPath=/data2")
    _assert_storage(docs, "/data2")
