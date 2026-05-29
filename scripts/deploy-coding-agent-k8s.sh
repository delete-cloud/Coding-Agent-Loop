#!/usr/bin/env sh
set -eu

IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-ghcr.io/delete-cloud/coding-agent}"
IMAGE_TAG="${IMAGE_TAG:-${CI_COMMIT_SHA:-}}"
K8S_NAMESPACE="${K8S_NAMESPACE:-coding-agent-deepseek}"
K8S_DEPLOYMENT="${K8S_DEPLOYMENT:-coding-agent-coding-agent}"
K8S_CONTAINER="${K8S_CONTAINER:-coding-agent}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
ENABLE_POD_HEALTH_SMOKE="${ENABLE_POD_HEALTH_SMOKE:-1}"

if [ -z "$IMAGE_TAG" ]; then
  printf '%s\n' "IMAGE_TAG or CI_COMMIT_SHA is required" >&2
  exit 2
fi

if [ -n "${KUBECONFIG_CONTENT:-}" ]; then
  kubeconfig_file="$(mktemp)"
  umask 077
  printf '%s' "$KUBECONFIG_CONTENT" > "$kubeconfig_file"
  export KUBECONFIG="$kubeconfig_file"
fi

image="${IMAGE_REPOSITORY}:${IMAGE_TAG}"

kubectl -n "$K8S_NAMESPACE" set image \
  "deployment/${K8S_DEPLOYMENT}" \
  "${K8S_CONTAINER}=${image}"

kubectl -n "$K8S_NAMESPACE" rollout status \
  "deployment/${K8S_DEPLOYMENT}" \
  --timeout="$ROLLOUT_TIMEOUT"

kubectl -n "$K8S_NAMESPACE" get deployment "$K8S_DEPLOYMENT" \
  -o "jsonpath={.spec.template.spec.containers[?(@.name=='${K8S_CONTAINER}')].image}{'\n'}"

if [ "$ENABLE_POD_HEALTH_SMOKE" = "1" ]; then
  kubectl -n "$K8S_NAMESPACE" exec "deployment/${K8S_DEPLOYMENT}" \
    -c "$K8S_CONTAINER" \
    -- python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=5).read().decode())'
fi
