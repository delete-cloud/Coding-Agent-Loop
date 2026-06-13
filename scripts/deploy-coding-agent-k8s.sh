#!/usr/bin/env sh
set -eu

IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-git.mesh.kinaz.me/kina/coding-agent}"
IMAGE_TAG="${IMAGE_TAG:-${CI_COMMIT_SHA:-}}"
K8S_NAMESPACE="${K8S_NAMESPACE:-coding-agent-deepseek}"
K8S_DEPLOYMENT="${K8S_DEPLOYMENT:-coding-agent-coding-agent}"
K8S_CONTAINER="${K8S_CONTAINER:-coding-agent}"
K8S_SERVICE="${K8S_SERVICE:-$K8S_DEPLOYMENT}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
ENABLE_POD_HEALTH_SMOKE="${ENABLE_POD_HEALTH_SMOKE:-1}"
HELM_RELEASE="${HELM_RELEASE:-coding-agent}"
HELM_CHART_DIR="${HELM_CHART_DIR:-coding-agent/helm}"
HELM_VALUES="${HELM_VALUES:-coding-agent/helm/values-o6n-deepseek.yaml}"
HELM_DEPLOY_MODE="${HELM_DEPLOY_MODE:-dry-run}"
HELM_DRIVER="${HELM_DRIVER:-configmap}"

if [ -z "$IMAGE_TAG" ]; then
  printf '%s\n' "IMAGE_TAG or CI_COMMIT_SHA is required" >&2
  exit 2
fi

if [ "$HELM_DEPLOY_MODE" != "dry-run" ] && [ "$HELM_DEPLOY_MODE" != "apply" ]; then
  printf '%s\n' "HELM_DEPLOY_MODE must be dry-run or apply" >&2
  exit 2
fi

if [ -n "${KUBECONFIG_CONTENT:-}" ]; then
  kubeconfig_file="$(mktemp)"
  umask 077
  printf '%s' "$KUBECONFIG_CONTENT" > "$kubeconfig_file"
  export KUBECONFIG="$kubeconfig_file"
fi

export HELM_DRIVER

if ! command -v helm >/dev/null 2>&1; then
  printf '%s\n' "helm is required for coding-agent deploy" >&2
  exit 2
fi

set -- \
  upgrade --install "$HELM_RELEASE" "$HELM_CHART_DIR" \
  --namespace "$K8S_NAMESPACE" \
  --values "$HELM_VALUES" \
  --set "image.repository=${IMAGE_REPOSITORY}" \
  --set "image.tag=${IMAGE_TAG}" \
  --wait \
  --timeout "$ROLLOUT_TIMEOUT"

if [ "$HELM_DEPLOY_MODE" = "dry-run" ]; then
  # Default mode proves the rendered release without mutating the cluster.
  helm "$@" --dry-run=client
  exit 0
fi

helm "$@"

kubectl -n "$K8S_NAMESPACE" rollout status \
  "deployment/${K8S_DEPLOYMENT}" \
  --timeout="$ROLLOUT_TIMEOUT"

kubectl -n "$K8S_NAMESPACE" get deployment "$K8S_DEPLOYMENT" \
  -o "jsonpath={.spec.template.spec.containers[?(@.name=='${K8S_CONTAINER}')].image}{'\n'}"

if [ "$ENABLE_POD_HEALTH_SMOKE" = "1" ]; then
  health_url="http://${K8S_SERVICE}.${K8S_NAMESPACE}.svc.cluster.local:8080/healthz"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "$health_url"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$health_url"
  elif command -v python >/dev/null 2>&1; then
    python - "$health_url" <<'PY'
import sys
from urllib.request import urlopen

with urlopen(sys.argv[1], timeout=10) as response:
    sys.stdout.write(response.read().decode())
PY
  else
    printf '%s\n' "curl, wget, or python is required for service health smoke" >&2
    exit 2
  fi
fi
