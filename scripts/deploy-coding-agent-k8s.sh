#!/usr/bin/env sh
set -eu

IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-<registry>/<namespace>/coding-agent}"
IMAGE_TAG="${IMAGE_TAG:-${CI_COMMIT_SHA:-}}"
K8S_NAMESPACE="${K8S_NAMESPACE:-coding-agent}"
K8S_DEPLOYMENT="${K8S_DEPLOYMENT:-coding-agent-coding-agent}"
K8S_CONTAINER="${K8S_CONTAINER:-coding-agent}"
K8S_SERVICE="${K8S_SERVICE:-$K8S_DEPLOYMENT}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
ENABLE_POD_HEALTH_SMOKE="${ENABLE_POD_HEALTH_SMOKE:-1}"
HELM_RELEASE="${HELM_RELEASE:-coding-agent}"
HELM_CHART_DIR="${HELM_CHART_DIR:-coding-agent/helm}"
HELM_VALUES="${HELM_VALUES:-coding-agent/helm/values-example.yaml}"
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

# Fail closed before mutating a cluster: apply must use real deploy values,
# not the genericized placeholders. dry-run intentionally tolerates them.
if [ "$HELM_DEPLOY_MODE" = "apply" ]; then
  case "$IMAGE_REPOSITORY" in
  *"<"*)
    printf '%s\n' "apply requires a real IMAGE_REPOSITORY (got placeholder)" >&2
    exit 2
    ;;
  esac
  if [ -z "${VALUES_CONTENT:-}" ] \
    && [ "$HELM_VALUES" = "coding-agent/helm/values-example.yaml" ]; then
    printf '%s\n' \
      "apply requires real values: set VALUES_CONTENT or HELM_VALUES" >&2
    exit 2
  fi
fi

if [ -n "${KUBECONFIG_CONTENT:-}" ]; then
  kubeconfig_file="$(mktemp)"
  umask 077
  printf '%s' "$KUBECONFIG_CONTENT" > "$kubeconfig_file"
  export KUBECONFIG="$kubeconfig_file"
fi

helm_values_file="$HELM_VALUES"
if [ -n "${VALUES_CONTENT:-}" ]; then
  helm_values_file="$(mktemp)"
  umask 077
  printf '%s' "$VALUES_CONTENT" > "$helm_values_file"
fi

export HELM_DRIVER

if ! command -v helm >/dev/null 2>&1; then
  printf '%s\n' "helm is required for coding-agent deploy" >&2
  exit 2
fi

set -- \
  upgrade --install "$HELM_RELEASE" "$HELM_CHART_DIR" \
  --namespace "$K8S_NAMESPACE" \
  --values "$helm_values_file" \
  --set "image.repository=${IMAGE_REPOSITORY}" \
  --set "image.tag=${IMAGE_TAG}" \
  --wait \
  --timeout "$ROLLOUT_TIMEOUT" \
  --take-ownership

if [ "$HELM_DEPLOY_MODE" = "dry-run" ]; then
  # Validate RBAC, ownership takeover, and admission without mutating the cluster.
  helm "$@" --dry-run=server
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
