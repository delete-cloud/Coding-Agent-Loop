from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from coding_agent.__main__ import create_agent


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


def _cronjob(docs: list[dict[str, Any]], name_suffix: str) -> dict[str, Any]:
    return _object_named(docs, "CronJob", name_suffix)


def _objects_of_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def _agent_toml(docs: list[dict[str, Any]]) -> dict[str, Any]:
    config = _object_named(docs, "ConfigMap", "-agent-config")
    return tomllib.loads(config["data"]["agent.toml"])


def _agent_toml_text(docs: list[dict[str, Any]]) -> str:
    config = _object_named(docs, "ConfigMap", "-agent-config")
    value = config["data"]["agent.toml"]
    assert isinstance(value, str)
    return value


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
        "values-example.yaml",
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
    config = _agent_toml(docs)
    assert "kb" not in config
    assert "kb" not in config["agent"]["plugins"]["enabled"]
    assert not _objects_of_kind(docs, "CronJob")

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


def test_helm_agent_config_checksum_annotation_changes_with_config(
    tmp_path: Path,
) -> None:
    default_docs = _render()
    custom_docs = _render("--set", "agent.config.model=claude-test-checksum")
    override_values = tmp_path / "values.yaml"
    override_values.write_text(
        "podAnnotations:\n"
        "  checksum/config: user-override-value\n",
        encoding="utf-8",
    )
    override_docs = _render("-f", str(override_values))

    default_annotations = _deployment(default_docs)["spec"]["template"]["metadata"][
        "annotations"
    ]
    custom_annotations = _deployment(custom_docs)["spec"]["template"]["metadata"][
        "annotations"
    ]
    override_annotations = _deployment(override_docs)["spec"]["template"]["metadata"][
        "annotations"
    ]

    assert re.fullmatch(r"[0-9a-f]{64}", default_annotations["checksum/config"])
    assert re.fullmatch(r"[0-9a-f]{64}", override_annotations["checksum/config"])
    assert override_annotations["checksum/config"] == default_annotations["checksum/config"]
    assert override_annotations["checksum/config"] != "user-override-value"
    assert custom_annotations["checksum/config"] != default_annotations["checksum/config"]


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


def test_helm_network_policy_preserves_explicit_empty_ingress_sources() -> None:
    docs = _render("--set-json", "networkPolicy.ingress.from=[]")
    policy = _network_policy(docs)

    assert policy["spec"]["ingress"][0]["from"] == []


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


def test_helm_agent_base_url_override_is_rendered() -> None:
    docs = _render("--set", "agent.config.baseUrl=https://llm-proxy.example/v1")

    assert _agent_toml(docs)["agent"]["base_url"] == "https://llm-proxy.example/v1"


def test_helm_example_values_preserve_template_runtime_contract() -> None:
    docs = _render("-f", str(CHART / "values-example.yaml"))

    service = _service(docs)
    assert service["spec"]["type"] == "NodePort"
    assert service["spec"]["ports"][0] == {
        "name": "http",
        "port": 8080,
        "targetPort": "http",
        "protocol": "TCP",
        "nodePort": 30080,
    }

    main = _container(docs)
    assert main["image"] == "<registry>/<namespace>/coding-agent:main"
    assert main["envFrom"] == [
        {"secretRef": {"name": "<provider-secret>"}},
        {"secretRef": {"name": "<observability-secret>"}},
    ]
    assert _env_var(main, "CODING_AGENT_API_KEY")["valueFrom"]["secretKeyRef"] == {
        "name": "coding-agent-coding-agent-api-key",
        "key": "api-key",
    }

    config = _agent_toml(docs)
    assert config["agent"]["provider"] == "openai"
    assert config["agent"]["model"] == "gpt-4.1"
    assert config["server"]["bearer_token_env"] == "CODING_AGENT_API_KEY"
    assert config["observability"] == {
        "enabled": True,
        "backend": "langfuse",
        "endpoint_env": "<observability-endpoint-env>",
        "timeout_seconds": 2,
        "public_key_env": "<observability-public-key-env>",
        "secret_key_env": "<observability-secret-key-env>",
    }

    policy = _network_policy(docs)
    assert policy["spec"]["egress"][-1] == {
        "to": [{"ipBlock": {"cidr": "<observability-host-ip>/32"}}],
        "ports": [{"protocol": "TCP", "port": 443}],
    }


def test_helm_example_config_bootstraps_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docs = _render("-f", str(CHART / "values-example.yaml"))
    config_path = tmp_path / "agent.toml"
    config_path.write_text(_agent_toml_text(docs), encoding="utf-8")
    monkeypatch.setenv(
        "<observability-endpoint-env>",
        "https://observability.example.test/api/public/otel",
    )
    monkeypatch.setenv("<observability-public-key-env>", "pk-test")
    monkeypatch.setenv("<observability-secret-key-env>", "sk-test")

    pipeline, _ = create_agent(
        config_path=config_path,
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        api_key="sk-test",
    )

    assert "topic" not in pipeline._registry.plugin_ids()
    assert "llm_provider" in pipeline._registry.plugin_ids()


def test_helm_workspace_mount_override_sets_working_dir() -> None:
    docs = _render("--set", "persistence.workspace.mountPath=/mnt/workspace")
    assert _container(docs)["workingDir"] == "/mnt/workspace"


def test_helm_data_mount_override_updates_sqlite_bundle_path() -> None:
    docs = _render("--set", "persistence.data.mountPath=/data2")
    _assert_storage(docs, "/data2")


def test_helm_kb_enabled_renders_config_and_plugin() -> None:
    docs = _render("--set", "kb.enabled=true")
    config = _agent_toml(docs)

    assert "kb" in config["agent"]["plugins"]["enabled"]
    assert config["kb"] == {
        "db_path": "/var/lib/coding-agent/data/kb",
        "embedding_model": "BAAI/bge-m3",
        "embedding_base_url": "https://api.siliconflow.cn/v1",
        "embedding_dim": 1024,
        "corpus": "notes",
        "search_corpora": ["sre", "notes"],
    }


def test_helm_kb_enabled_can_render_optional_max_distance() -> None:
    docs = _render("--set", "kb.enabled=true", "--set", "kb.maxDistance=0.42")

    assert _agent_toml(docs)["kb"]["max_distance"] == 0.42


def test_helm_kb_index_disabled_does_not_render_cronjob() -> None:
    docs = _render("--set", "kb.enabled=true", "--set", "kb.index.enabled=false")

    assert not _objects_of_kind(docs, "CronJob")


def test_helm_kb_index_cronjob_excluded_from_netpol() -> None:
    docs = _render(
        "--set",
        "kb.enabled=true",
        "--set",
        "kb.index.enabled=true",
        "--set",
        "kb.index.gitSecretName=kb-git-creds",
        "--set-json",
        'agent.secretEnv=[{"name":"OPENAI_API_KEY","secretName":"kb-embedding","secretKey":"api-key"}]',
        "--set-json",
        'kb.index.repos=[{"name":"sre","corpus":"sre","url":"<forgejo-host>/<owner>/sre.git"}]',
    )
    job = _cronjob(docs, "-kb-index")
    pod_spec = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert job["spec"]["schedule"] == "0 * * * *"
    # The index Job must be EXCLUDED from the agent NetworkPolicy so it can reach
    # the private/mesh git host. Its pod labels must therefore NOT satisfy the
    # netpol podSelector (which is the agent selectorLabels).
    job_labels = job["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
    assert job_labels == {
        "app.kubernetes.io/name": "coding-agent-kb-index",
        "app.kubernetes.io/instance": "coding-agent",
        "app.kubernetes.io/component": "kb-index",
    }
    netpol_selector = _network_policy(docs)["spec"]["podSelector"]["matchLabels"]
    assert not (netpol_selector.items() <= job_labels.items()), (
        "kb-index Job must not match the agent NetworkPolicy podSelector"
    )
    assert pod_spec["securityContext"]["fsGroup"] == 10001
    assert container["securityContext"] == {
        "runAsGroup": 10001,
        "runAsNonRoot": True,
        "runAsUser": 10001,
    }
    assert container["envFrom"] == [{"secretRef": {"name": "kb-git-creds"}}]
    assert _env_var(container, "OPENAI_API_KEY")["valueFrom"]["secretKeyRef"] == {
        "name": "kb-embedding",
        "key": "api-key",
    }
    # The CronJob must mount the SAME rendered agent.toml ConfigMap as the
    # Deployment, at the same configPath/subPath, so `kb index` reads the [kb]
    # embedding/db_path config from values rather than the image-baked default.
    deployment_container = _container(docs)
    config_mount = next(
        m for m in deployment_container["volumeMounts"] if m["name"] == "agent-config"
    )
    assert config_mount["subPath"] == "agent.toml"
    assert container["volumeMounts"] == [
        {
            "name": "agent-config",
            "mountPath": config_mount["mountPath"],
            "subPath": "agent.toml",
            "readOnly": True,
        },
        {
            "name": "data-storage",
            "mountPath": "/var/lib/coding-agent/data",
        },
    ]
    assert pod_spec["volumes"] == [
        {
            "name": "agent-config",
            "configMap": {"name": "coding-agent-coding-agent-agent-config"},
        },
        {
            "name": "data-storage",
            "persistentVolumeClaim": {
                "claimName": "coding-agent-coding-agent-data",
            },
        },
    ]
    script = "\n".join(container["command"])
    assert "git clone --depth 1 --branch \"main\"" in script
    assert "<forgejo-host>/<owner>/sre.git" in script
    assert "/app/.venv/bin/python -m coding_agent kb index" in script
    assert re.search(r"(^|\n)\s*kb\s+index(\s|$)", script) is None
    assert "--corpus \"sre\"" in script


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
