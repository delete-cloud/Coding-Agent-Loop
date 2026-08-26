# Woodpecker Deployment

This repository uses Woodpecker for self-hosted CI/CD on the private mesh.

The deployment pipeline intentionally keeps runtime provider credentials in
Kubernetes Secrets. Woodpecker builds and deploys images, but it does not bake
provider, observability, bearer token, or kube credentials into the image.

Real values live in the private SRE inventory and Woodpecker repository secrets:
`deploy_values`, `kubeconfig`, `k8s_namespace`, and `image_repository`.

## Pipelines

- `.woodpecker/ci.yml` runs lint, focused smoke tests, and builds the HTTP
  server image.
- `.woodpecker/deploy.yml` is manual-only on `main` and runs the Helm deploy
  script against values supplied by the `deploy_values` Woodpecker secret. It
  defaults to `HELM_DEPLOY_MODE=dry-run` so the first pass uses Helm server-side
  dry-run to validate RBAC, ownership takeover, and admission without mutating
  the cluster.

The manual deploy step uses the build-registry deploy-tools image built by
`.woodpecker/ci.yml`:

```text
<build-registry>/<namespace>/coding-agent-deploy-tools:kubectl-1.36.0-helm-3.17.3-python-3.12-slim
```

The default image target is:

```text
<registry>/<namespace>/coding-agent:${CI_COMMIT_SHA}
```

The deploy workflow updates:

```text
namespace: <k8s-namespace>
release: coding-agent
values: VALUES_CONTENT from deploy_values
deployment: coding-agent-coding-agent, when HELM_DEPLOY_MODE=apply
```

## Required Woodpecker Secrets

Configure these in Woodpecker repository secrets:

```text
registry_username
registry_password
kubeconfig
deploy_values
k8s_namespace
image_repository
```

`registry_username` and `registry_password` must be able to push
`<registry>/<namespace>/coding-agent`.

`kubeconfig` should be scoped to the target cluster and preferably to the
`<k8s-namespace>` namespace.

Dry-run mode uses `helm upgrade --install --dry-run=server`, not local-only
template rendering. It therefore needs the same Kubernetes API visibility and
chart resource permissions that the preflight is intended to validate. This
deploy script sets `HELM_DRIVER=configmap` by default so Helm stores release
metadata in ConfigMaps instead of Secrets.

Apply mode needs permission to manage the chart-owned resources in the
`<k8s-namespace>` namespace:

- Deployment, Service, ConfigMap, PVC, ServiceAccount, and NetworkPolicy
- ConfigMaps used by Helm release metadata
- ReplicaSet/Pod read access for rollout status

Do not grant Secret read/write permission to the deploy identity. Runtime
Secrets are pre-created by the cluster operator.

The deploy script passes `--take-ownership` to Helm. This is intentional for the
first cutover from manually-created resources to a Helm-managed release; confirm
that same-name resources in the namespace belong to this deployment before
switching from dry-run to apply.

## Existing Kubernetes Secrets

Keep runtime secrets in Kubernetes, not this public repository. The deployment
values contain only Secret names and key names; Secret values should be supplied
by ordinary Kubernetes Secrets or SealedSecrets in the private SRE repo.

```bash
kubectl -n <k8s-namespace> get secret <provider-secret>
kubectl -n <k8s-namespace> get secret <observability-secret>
```

The deployment pipeline does not create or replace these secrets.

The chart values expect:

```text
coding-agent-coding-agent-api-key: api-key
<provider-secret>: <provider-api-key-env>
<observability-secret>: <observability-endpoint-env>, <observability-public-key-env>, <observability-secret-key-env>
```

`<observability-endpoint-env>` must be the OTLP base endpoint accepted by the
Coding Agent exporter; it is not the UI root URL unless the exporter endpoint is
served there.

Verify key names from the cluster before switching apply mode on. Do not print
or commit Secret values.

## Manual Deploy

Trigger `.woodpecker/deploy.yml` manually from the Woodpecker UI on `main`.
By default this is a Helm server-side dry-run.

Optional overrides can be set as Woodpecker environment variables:

```text
IMAGE_REPOSITORY
IMAGE_TAG
K8S_NAMESPACE
K8S_DEPLOYMENT
K8S_CONTAINER
K8S_SERVICE
ROLLOUT_TIMEOUT
ENABLE_POD_HEALTH_SMOKE
HELM_RELEASE
HELM_CHART_DIR
HELM_VALUES
VALUES_CONTENT
HELM_DEPLOY_MODE
HELM_DRIVER
```

Set `HELM_DEPLOY_MODE=apply` only after the server-side dry-run has passed, the
target namespace RBAC can manage chart-owned resources, and any same-name
resources in the namespace are confirmed to be the existing coding-agent
deployment that Helm may take over.

The health smoke calls `http://$K8S_SERVICE.$K8S_NAMESPACE.svc.cluster.local:8080/healthz`
from the pipeline container. Set `ENABLE_POD_HEALTH_SMOKE=0` if the runner cannot
resolve or reach Kubernetes Service DNS.
