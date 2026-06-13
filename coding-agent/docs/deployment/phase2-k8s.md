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
- The pod uses a dedicated ServiceAccount by default, does not mount a
  ServiceAccount token, and the chart does not create RBAC bindings.
- Kubernetes service environment variable injection is disabled with
  `enableServiceLinks=false`.
- NetworkPolicy is enabled by default. It allows inbound HTTP to the agent pod,
  DNS to CoreDNS/kube-dns, and outbound public HTTPS while excluding private,
  link-local, loopback, and carrier-grade NAT ranges.
- HTTPRoute support is optional and disabled by default.

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
- CNI NetworkPolicy enforcement. Do not treat rendered NetworkPolicy objects as
  an isolation boundary until the target cluster is confirmed to enforce them.
- Resource requests and limits for shared clusters.
- Gateway API and HTTPRoute parentRefs, if the chart is responsible for the
  public route.

## Shared Or Public Cluster Prerequisites

Do not deploy the unauthenticated Orbstack values to a public or shared cluster:
they are only for local development and intentionally set
`server.allowUnauthenticated=true`.

Before deploying to a shared cluster:

- Confirm the CNI enforces Kubernetes NetworkPolicy. A cluster may accept
  NetworkPolicy manifests without enforcing them.
- Set resource requests and limits. The default chart leaves `resources` empty
  so operators can choose values that match the target node.
- Keep the generated ServiceAccount unprivileged. The chart does not create
  Role, ClusterRole, RoleBinding, or ClusterRoleBinding resources.
- Keep `automountServiceAccountToken=false` unless there is a reviewed reason to
  let the pod call the Kubernetes API.
- If `httpRoute.enabled=true`, ensure the Gateway API CRDs exist and TLS is
  terminated by the Gateway, Traefik, or another reviewed edge component.
- If the provider endpoint, proxy, or dependency is on a private CIDR, add a
  narrow `networkPolicy.extraEgress` rule for that destination. The default
  public HTTPS rule intentionally excludes private address ranges, including
  `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`,
  `169.254.0.0/16`, and `127.0.0.0/8`.
- If the target cluster cannot enforce NetworkPolicy, do not count it as a safe
  shared-cluster deployment target for this service.

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
  --set-json cors.origins='["https://<agent-domain>"]' \
  --set-json resources.requests='{"cpu":"250m","memory":"512Mi"}' \
  --set-json resources.limits='{"cpu":"1","memory":"2Gi"}'
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
  --set-json cors.origins='["https://<agent-domain>"]' \
  --set-json resources.requests='{"cpu":"250m","memory":"512Mi"}' \
  --set-json resources.limits='{"cpu":"1","memory":"2Gi"}'
```

If the target cluster has no default storage class, set PVC classes explicitly:

```bash
--set persistence.workspace.storageClassName='<storage-class>' \
--set persistence.data.storageClassName='<storage-class>'
```

If the chart should render the Gateway API route, provide the parent Gateway and
hostname explicitly:

```bash
--set httpRoute.enabled=true \
--set-json httpRoute.parentRefs='[{"name":"<gateway-name>","namespace":"<gateway-namespace>"}]' \
--set-json httpRoute.hostnames='["<agent-domain>"]'
```

If a private provider endpoint or egress proxy is required, add only that
destination:

```bash
--set-json networkPolicy.extraEgress='[{"to":[{"ipBlock":{"cidr":"<provider-or-proxy-cidr>"}}],"ports":[{"protocol":"TCP","port":443}]}]'
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
