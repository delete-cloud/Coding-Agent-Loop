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


def _service_account(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return _object_named(docs, "ServiceAccount", "coding-agent")


def _network_policy(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return _object_named(docs, "NetworkPolicy", "coding-agent")


def _objects_of_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def _agent_toml(docs: list[dict[str, Any]]) -> dict[str, Any]:
    config = _object_named(docs, "ConfigMap", "-agent-config")
    return tomllib.loads(config["data"]["agent.toml"])


def _env_var(container: dict[str, Any], name: str) -> dict[str, Any]:
    for item in container.get("env", []):
        if item.get("name") == name:
            return item
    raise AssertionError(f"missing env var {name}")


def _command_option(command: list[str], option: str) -> str:
    try:
        index = command.index(option)
    except ValueError as exc:
        raise AssertionError(f"missing command option {option}") from exc
    try:
        return command[index + 1]
    except IndexError as exc:
        raise AssertionError(f"missing value for command option {option}") from exc


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
    assert pod_spec["serviceAccountName"] == "coding-agent-coding-agent"
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["enableServiceLinks"] is False

    main = _container(docs)
    assert main["securityContext"] == {
        "runAsGroup": 10001,
        "runAsNonRoot": True,
        "runAsUser": 10001,
    }
    assert main["ports"][0]["containerPort"] == 8080
    assert _command_option(main["command"], "--port") == "8080"
    assert (
        _command_option(main["command"], "--config")
        == "/app/src/coding_agent/agent.toml"
    )
    assert "--allow-unauthenticated" not in main["command"]


def test_helm_default_creates_unprivileged_service_account_without_rbac() -> None:
    docs = _render()

    service_account = _service_account(docs)
    assert service_account["apiVersion"] == "v1"
    assert service_account["automountServiceAccountToken"] is False

    assert not _objects_of_kind(docs, "Role")
    assert not _objects_of_kind(docs, "ClusterRole")
    assert not _objects_of_kind(docs, "RoleBinding")
    assert not _objects_of_kind(docs, "ClusterRoleBinding")


def test_helm_service_account_can_be_reused_without_creation() -> None:
    docs = _render(
        "--set",
        "serviceAccount.create=false",
        "--set",
        "serviceAccount.name=existing-agent-sa",
    )

    assert not _objects_of_kind(docs, "ServiceAccount")
    pod_spec = _deployment(docs)["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "existing-agent-sa"
    assert pod_spec["automountServiceAccountToken"] is False


def test_helm_default_network_policy_limits_ingress_and_egress() -> None:
    docs = _render()
    policy = _network_policy(docs)
    spec = policy["spec"]

    assert spec["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "coding-agent",
        "app.kubernetes.io/instance": "coding-agent",
    }
    assert spec["policyTypes"] == ["Ingress", "Egress"]
    assert spec["ingress"] == [
        {
            "ports": [
                {
                    "port": 8080,
                    "protocol": "TCP",
                }
            ]
        }
    ]

    dns_rule = spec["egress"][0]
    assert dns_rule["to"] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
            },
            "podSelector": {
                "matchExpressions": [
                    {
                        "key": "k8s-app",
                        "operator": "In",
                        "values": ["kube-dns", "coredns"],
                    }
                ]
            },
        }
    ]
    assert dns_rule["ports"] == [
        {"port": 53, "protocol": "UDP"},
        {"port": 53, "protocol": "TCP"},
    ]

    https_rule = spec["egress"][1]
    assert https_rule["to"] == [
        {
            "ipBlock": {
                "cidr": "0.0.0.0/0",
                "except": [
                    "10.0.0.0/8",
                    "172.16.0.0/12",
                    "192.168.0.0/16",
                    "100.64.0.0/10",
                    "169.254.0.0/16",
                    "127.0.0.0/8",
                ],
            }
        }
    ]
    assert https_rule["ports"] == [{"port": 443, "protocol": "TCP"}]


def test_helm_network_policy_can_be_disabled() -> None:
    docs = _render("--set", "networkPolicy.enabled=false")

    assert not _objects_of_kind(docs, "NetworkPolicy")


def test_helm_network_policy_requires_at_least_one_direction_when_enabled() -> None:
    result = subprocess.run(
        [
            _helm(),
            "template",
            "coding-agent",
            str(CHART),
            "--set",
            "networkPolicy.ingress.enabled=false",
            "--set",
            "networkPolicy.egress.enabled=false",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode != 0
    assert (
        "networkPolicy.ingress.enabled or networkPolicy.egress.enabled must be true "
        "when networkPolicy.enabled=true"
    ) in result.stderr


def test_helm_network_policy_renders_extra_rules() -> None:
    docs = _render(
        "--set-json",
        'networkPolicy.extraIngress[0]={"from":[{"namespaceSelector":{"matchLabels":{"gateway":"public"}}}],"ports":[{"protocol":"TCP","port":8080}]}',
        "--set-json",
        'networkPolicy.extraEgress[0]={"to":[{"ipBlock":{"cidr":"203.0.113.0/24"}}],"ports":[{"protocol":"TCP","port":8443}]}',
    )
    policy = _network_policy(docs)

    assert policy["spec"]["ingress"][-1] == {
        "from": [{"namespaceSelector": {"matchLabels": {"gateway": "public"}}}],
        "ports": [{"protocol": "TCP", "port": 8080}],
    }
    assert policy["spec"]["egress"][-1] == {
        "to": [{"ipBlock": {"cidr": "203.0.113.0/24"}}],
        "ports": [{"protocol": "TCP", "port": 8443}],
    }


def test_helm_httproute_is_disabled_by_default() -> None:
    docs = _render()

    assert not _objects_of_kind(docs, "HTTPRoute")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["--set", "httpRoute.enabled=true"],
            "httpRoute.parentRefs must be set when httpRoute.enabled=true",
        ),
        (
            [
                "--set",
                "httpRoute.enabled=true",
                "--set-json",
                'httpRoute.parentRefs=[{"name":"public-gateway"}]',
            ],
            "httpRoute.hostnames must be set when httpRoute.enabled=true",
        ),
    ],
)
def test_helm_httproute_requires_explicit_gateway_and_hostname(
    args: list[str], message: str
) -> None:
    result = subprocess.run(
        [_helm(), "template", "coding-agent", str(CHART), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_helm_httproute_renders_gateway_backend_and_hostname() -> None:
    docs = _render(
        "--set",
        "httpRoute.enabled=true",
        "--set-json",
        'httpRoute.parentRefs=[{"name":"public-gateway","namespace":"traefik"}]',
        "--set-json",
        'httpRoute.hostnames=["agent.example.com"]',
    )
    route = _object_named(docs, "HTTPRoute", "coding-agent")

    assert route["apiVersion"] == "gateway.networking.k8s.io/v1"
    assert route["spec"]["parentRefs"] == [
        {"name": "public-gateway", "namespace": "traefik"}
    ]
    assert route["spec"]["hostnames"] == ["agent.example.com"]
    assert route["spec"]["rules"] == [
        {
            "matches": [
                {
                    "path": {
                        "type": "PathPrefix",
                        "value": "/",
                    }
                }
            ],
            "backendRefs": [
                {
                    "name": "coding-agent-coding-agent",
                    "port": 8080,
                }
            ],
        }
    ]


def test_helm_default_configures_http_auth() -> None:
    docs = _render()
    server = _agent_toml(docs)["server"]
    assert server["bearer_token_env"] == "CODING_AGENT_API_KEY"

    main = _container(docs)
    assert (
        _command_option(main["command"], "--config")
        == "/app/src/coding_agent/agent.toml"
    )

    token_env = _env_var(main, "CODING_AGENT_API_KEY")
    assert token_env["valueFrom"]["secretKeyRef"] == {
        "name": "coding-agent-coding-agent-api-key",
        "key": "api-key",
    }


def test_helm_default_exposes_webui_static_dir_and_cors_whitelist() -> None:
    docs = _render()
    main = _container(docs)

    assert _env_var(main, "WEBUI_DIST_DIR") == {
        "name": "WEBUI_DIST_DIR",
        "value": "/app/webui-dist",
    }
    assert _env_var(main, "CODING_AGENT_CORS_ORIGINS") == {
        "name": "CODING_AGENT_CORS_ORIGINS",
        "value": "https://agent.example.com",
    }


def test_helm_can_disable_bundled_webui_static_mount() -> None:
    docs = _render("--set", "webui.enabled=false")
    main = _container(docs)

    assert _env_var(main, "WEBUI_DIST_DIR") == {
        "name": "WEBUI_DIST_DIR",
        "value": "",
    }


def test_helm_cors_whitelist_can_be_disabled_for_local_values() -> None:
    docs = _render("-f", str(CHART / "values-orbstack.yaml"))
    main = _container(docs)

    with pytest.raises(
        AssertionError, match="missing env var CODING_AGENT_CORS_ORIGINS"
    ):
        _env_var(main, "CODING_AGENT_CORS_ORIGINS")


@pytest.mark.parametrize(
    "values_file",
    [
        "values-orbstack.yaml",
        "values-orbstack-kimi.yaml",
    ],
)
def test_helm_local_values_disable_http_auth_secret(values_file: str) -> None:
    docs = _render("-f", str(CHART / values_file))
    server = _agent_toml(docs)["server"]
    assert "bearer_token_env" not in server

    main = _container(docs)
    with pytest.raises(AssertionError, match="missing env var CODING_AGENT_API_KEY"):
        _env_var(main, "CODING_AGENT_API_KEY")


@pytest.mark.parametrize(
    "values_file",
    [
        "values-orbstack.yaml",
        "values-orbstack-kimi.yaml",
    ],
)
def test_helm_local_values_explicitly_allow_unauthenticated_listener(
    values_file: str,
) -> None:
    docs = _render("-f", str(CHART / values_file))
    main = _container(docs)

    assert "--allow-unauthenticated" in main["command"]
    assert _command_option(main["command"], "--host") == "0.0.0.0"


def test_helm_service_port_override_keeps_app_port() -> None:
    docs = _render("--set", "service.port=9090")
    assert _service(docs)["spec"]["ports"][0]["port"] == 9090

    main = _container(docs)
    assert main["ports"][0]["containerPort"] == 8080
    assert _command_option(main["command"], "--port") == "8080"
    assert (
        _command_option(main["command"], "--config")
        == "/app/src/coding_agent/agent.toml"
    )


def test_helm_workspace_mount_override_sets_working_dir() -> None:
    docs = _render("--set", "persistence.workspace.mountPath=/mnt/workspace")
    assert _container(docs)["workingDir"] == "/mnt/workspace"


def test_helm_data_mount_override_updates_sqlite_bundle_path() -> None:
    docs = _render("--set", "persistence.data.mountPath=/data2")
    _assert_storage(docs, "/data2")


def test_helm_chart_ignores_legacy_sandbox_sidecar_values() -> None:
    docs = _render("--set", "sandbox.sidecar.enabled=true")
    assert all(
        not doc.get("metadata", {}).get("name", "").endswith("-sandbox-config")
        for doc in _objects_of_kind(docs, "ConfigMap")
    )

    pod_spec = _deployment(docs)["spec"]["template"]["spec"]
    assert [container["name"] for container in pod_spec["containers"]] == [
        "coding-agent"
    ]
    main = _container(docs)
    assert all(
        env.get("name") != "SANDBOX_NSJAIL_CONFIG_PATH" for env in main.get("env", [])
    )
    assert all(
        mount.get("name") != "sandbox-config" for mount in main.get("volumeMounts", [])
    )
    assert all(volume.get("name") != "sandbox-config" for volume in pod_spec["volumes"])


def test_runtime_image_installs_native_linux_sandbox_binary() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    install_block = dockerfile.split("apt-get install", maxsplit=1)[1].split(
        "&& rm", maxsplit=1
    )[0]
    assert "bubblewrap" in install_block


def test_runtime_image_builds_and_copies_webui_dist() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "FROM node:20-alpine AS webui" in dockerfile
    assert "ARG PNPM_VERSION=" in dockerfile
    assert 'corepack prepare "pnpm@${PNPM_VERSION}" --activate' in dockerfile
    assert (
        "COPY webui/app/package.json webui/app/pnpm-lock.yaml webui/app/pnpm-workspace.yaml"
        in dockerfile
    )
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "pnpm build" in dockerfile
    assert (
        "COPY --chown=coding-agent:coding-agent --from=webui /webui/app/dist /app/webui-dist"
        in dockerfile
    )
    assert 'WEBUI_DIST_DIR="/app/webui-dist"' in dockerfile
