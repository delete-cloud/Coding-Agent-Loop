from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "deploy-coding-agent-k8s.sh"
DEPLOY_TOOLS_TAG = "kubectl-1.36.0-helm-3.17.3-python-3.12-slim"


def _write_stub(directory: Path, name: str) -> None:
    path = directory / name
    path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\' "$0" >> "$CALL_LOG"\n'
        'printf \' %s\' "$@" >> "$CALL_LOG"\n'
        "printf '\\n' >> \"$CALL_LOG\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_script(
    tmp_path: Path, **env_overrides: str
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "helm")
    _write_stub(bin_dir, "kubectl")
    _write_stub(bin_dir, "curl")

    call_log = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CALL_LOG": str(call_log),
        "IMAGE_TAG": "test-sha",
    }
    env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_deploy_script_defaults_to_helm_dry_run_without_kubectl_mutation(
    tmp_path: Path,
) -> None:
    result = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "helm upgrade --install coding-agent coding-agent/helm" in calls
    assert "--namespace coding-agent" in calls
    assert "--values coding-agent/helm/values-example.yaml" in calls
    assert "--set image.repository=<registry>/<namespace>/coding-agent" in calls
    assert "--set image.tag=test-sha" in calls
    assert "--take-ownership" in calls
    assert "--dry-run=server" in calls
    assert "--dry-run=client" not in calls
    assert "kubectl" not in calls


def test_deploy_script_apply_mode_waits_for_rollout_and_smokes_service(
    tmp_path: Path,
) -> None:
    result = _run_script(
        tmp_path,
        HELM_DEPLOY_MODE="apply",
        IMAGE_REPOSITORY="example.invalid/coding-agent",
        VALUES_CONTENT="image:\n  repository: example.invalid/coding-agent\n",
    )

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "helm upgrade --install coding-agent coding-agent/helm" in calls
    assert "--take-ownership" in calls
    assert "--dry-run" not in calls
    assert "kubectl -n coding-agent rollout status" in calls
    assert (
        "curl -fsS http://coding-agent-coding-agent.coding-agent.svc.cluster.local:8080/healthz"
        in calls
    )


def test_deploy_script_apply_mode_rejects_placeholder_image_repository(
    tmp_path: Path,
) -> None:
    # Guard: apply must not run against the genericized placeholder defaults.
    result = _run_script(tmp_path, HELM_DEPLOY_MODE="apply")

    assert result.returncode == 2
    assert "apply requires a real IMAGE_REPOSITORY" in result.stderr
    assert not (tmp_path / "calls.log").exists()


def test_deploy_script_apply_mode_rejects_example_values_without_content(
    tmp_path: Path,
) -> None:
    result = _run_script(
        tmp_path,
        HELM_DEPLOY_MODE="apply",
        IMAGE_REPOSITORY="example.invalid/coding-agent",
    )

    assert result.returncode == 2
    assert "apply requires real values" in result.stderr
    assert not (tmp_path / "calls.log").exists()


def test_deploy_script_uses_values_content_temp_file_when_present(
    tmp_path: Path,
) -> None:
    values_content = "image:\n  repository: example.invalid/coding-agent\n"
    result = _run_script(tmp_path, VALUES_CONTENT=values_content)

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    helm_call = next(line for line in calls.splitlines() if "helm upgrade" in line)
    parts = helm_call.split()
    values_path = Path(parts[parts.index("--values") + 1])
    assert values_path.read_text(encoding="utf-8") == values_content
    assert "coding-agent/helm/values-example.yaml" not in helm_call


def test_woodpecker_builds_deploy_tools_image_used_by_manual_deploy() -> None:
    ci = (ROOT / ".woodpecker" / "ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / ".woodpecker" / "deploy.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "deploy-tools.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "git.mesh.kinaz.me" not in ci
    assert "git.mesh.kinaz.me" not in deploy
    assert 'image: "${DEPLOY_TOOLS_IMAGE}"' in deploy
    assert "from_secret: deploy_tools_image" in deploy
    assert "from_secret: registry_host" in ci
    assert "from_secret: registry_namespace" in ci
    assert (
        '--destination "${REGISTRY_HOST}/${REGISTRY_NAMESPACE}/'
        'coding-agent-deploy-tools:${DEPLOY_TOOLS_TAG}"'
    ) in ci
    assert f"DEPLOY_TOOLS_TAG: {DEPLOY_TOOLS_TAG}" in ci
    assert "ARG KUBECTL_VERSION=1.36.0" in dockerfile
    assert "ARG HELM_VERSION=3.17.3" in dockerfile
    assert "/usr/local/bin/kubectl" in dockerfile
    assert "/usr/local/bin/helm" in dockerfile
    assert "kubectl.sha256" in dockerfile
    assert "helm.tgz.sha256sum" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "ENV HOME=/home/deploy" in dockerfile
    assert "USER 10001:10001" in dockerfile
