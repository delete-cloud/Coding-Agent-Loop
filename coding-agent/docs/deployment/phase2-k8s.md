# Phase 2 k8s deployment runbook

This runbook captures the code-side deployment path for the Coding Agent web UI
runtime image. It intentionally leaves hostnames, IP addresses, registry names,
and secrets as operator-provided values.

## Current Contract

- Runtime image builds the React web UI and copies it to `/app/webui-dist`.
- The container sets `WEBUI_DIST_DIR=/app/webui-dist`.
- The server mounts `WEBUI_DIST_DIR` through FastAPI `StaticFiles`.
- The Helm chart runs as UID/GID `10001`, uses native sandbox mode by default,
  and mounts separate `workspace` and `data` PVCs.
- The default Service is `ClusterIP` on port `8080`.
- HTTP bearer auth is enabled by default. The chart expects an existing Secret.

## Required Operator Decisions

Fill these before live deployment:

- Kubernetes context: `<kube-context>`
- Namespace: `coding-agent`
- Image repository: `<registry>/<namespace>/coding-agent`
- Image tag: `<image-tag>`
- Public URL: `https://<agent-domain>`
- TLS path: ingress/cert-manager, existing reverse proxy, or temporary
  `kubectl port-forward`
- Auth Secret value: `<web-api-bearer-token>`
- Provider and model:
  - default chart: `provider=copilot`, `model=gpt-4.1`
  - Kimi values: `provider=kimi-code`, `model=kimi-for-coding`
- Provider Secret values, if required by the selected provider
- Storage class and PVC sizes, if the cluster default is not acceptable

## Build And Publish

Build locally:

```bash
docker build -t <registry>/<namespace>/coding-agent:<image-tag> .
```

Push to the selected registry:

```bash
docker push <registry>/<namespace>/coding-agent:<image-tag>
```

The CI gate also runs a non-pushing image build:

```bash
docker build -t coding-agent:ci .
```

## Namespace And Secrets

Create the namespace:

```bash
kubectl --context <kube-context> create namespace coding-agent \
  --dry-run=client -o yaml \
  | kubectl --context <kube-context> apply -f -
```

Create the server bearer token Secret:

```bash
kubectl --context <kube-context> -n coding-agent create secret generic coding-agent-coding-agent-api-key \
  --from-literal=api-key='<web-api-bearer-token>' \
  --dry-run=client -o yaml \
  | kubectl --context <kube-context> apply -f -
```

For Kimi deployments, also create:

```bash
kubectl --context <kube-context> -n coding-agent create secret generic coding-agent-kimi \
  --from-literal=KIMI_CODE_API_KEY='<kimi-code-api-key>' \
  --dry-run=client -o yaml \
  | kubectl --context <kube-context> apply -f -
```

Do not commit these Secret values to this repository or to the SRE inventory.

## Install Or Upgrade

Default provider:

```bash
helm --kube-context <kube-context> upgrade --install coding-agent ./helm \
  --namespace coding-agent \
  --set image.repository='<registry>/<namespace>/coding-agent' \
  --set image.tag='<image-tag>' \
  --set-json cors.origins='["https://<agent-domain>"]'
```

Kimi provider:

```bash
helm --kube-context <kube-context> upgrade --install coding-agent ./helm \
  --namespace coding-agent \
  -f helm/values-orbstack-kimi.yaml \
  --set image.repository='<registry>/<namespace>/coding-agent' \
  --set image.tag='<image-tag>' \
  --set server.auth.enabled=true \
  --set server.allowUnauthenticated=false \
  --set-json cors.origins='["https://<agent-domain>"]'
```

If the target cluster has no default storage class, set PVC classes explicitly:

```bash
--set persistence.workspace.storageClassName='<storage-class>' \
--set persistence.data.storageClassName='<storage-class>'
```

## Health Checks

Wait for rollout:

```bash
kubectl --context <kube-context> -n coding-agent rollout status deploy/coding-agent-coding-agent
```

Inspect resources:

```bash
kubectl --context <kube-context> -n coding-agent get pods,svc,pvc
```

Smoke test through port-forward:

```bash
kubectl --context <kube-context> -n coding-agent port-forward svc/coding-agent-coding-agent 8080:8080
curl -fsS -H "Authorization: Bearer <web-api-bearer-token>" http://127.0.0.1:8080/readyz
curl -fsS -H "Authorization: Bearer <web-api-bearer-token>" http://127.0.0.1:8080/healthz
```

Verify the bundled web UI:

```bash
curl -fsS -H "Authorization: Bearer <web-api-bearer-token>" http://127.0.0.1:8080/ | head
```

## Rollback

List releases:

```bash
helm --kube-context <kube-context> -n coding-agent history coding-agent
```

Rollback to a known-good revision:

```bash
helm --kube-context <kube-context> -n coding-agent rollback coding-agent <revision>
kubectl --context <kube-context> -n coding-agent rollout status deploy/coding-agent-coding-agent
```

## SRE Inventory Fields

Record these in the SRE source of truth after the target is chosen:

- Service name: `coding-agent`
- Environment: `<env>`
- Kubernetes context/cluster: `<kube-context>`
- Namespace: `coding-agent`
- Public DNS name: `<agent-domain>`
- Ingress/TLS owner: `<ingress-or-proxy-owner>`
- Image repository: `<registry>/<namespace>/coding-agent`
- PVCs:
  - `coding-agent-coding-agent-workspace`
  - `coding-agent-coding-agent-data`
- Secret objects:
  - `coding-agent-coding-agent-api-key` (`api-key`)
  - optional `coding-agent-kimi` (`KIMI_CODE_API_KEY`)
- Restore smoke command:
  `curl -fsS -H "Authorization: Bearer <token>" https://<agent-domain>/readyz`

Keep plaintext secret material out of inventory.
