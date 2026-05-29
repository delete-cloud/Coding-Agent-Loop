# Woodpecker Deployment

This repository uses Woodpecker for self-hosted CI/CD on the private mesh.

The deployment pipeline intentionally keeps runtime provider credentials in
Kubernetes Secrets. Woodpecker builds and deploys images, but it does not bake
DeepSeek, Langfuse, bearer tokens, or kube credentials into the image.

## Pipelines

- `.woodpecker/ci.yml` runs lint, focused smoke tests, and builds the HTTP
  server image.
- `.woodpecker/deploy.yml` is manual-only on `main` and rolls the existing
  Kubernetes deployment to the built image tag.

The default image target is:

```text
ghcr.io/delete-cloud/coding-agent:${CI_COMMIT_SHA}
```

The deploy workflow updates:

```text
namespace: coding-agent-deepseek
deployment: coding-agent-coding-agent
container: coding-agent
```

## Required Woodpecker Secrets

Configure these in Woodpecker repository secrets:

```text
registry_username
registry_password
kubeconfig
```

`registry_username` and `registry_password` must be able to push
`ghcr.io/delete-cloud/coding-agent`.

`kubeconfig` should be scoped to the target cluster and preferably to the
`coding-agent-deepseek` namespace. The service account only needs permission to:

- get deployments
- patch deployments
- watch rollout status
- exec into the coding-agent pod for `/healthz` smoke

## Existing Kubernetes Secrets

Keep runtime secrets in Kubernetes, not Woodpecker:

```bash
kubectl -n coding-agent-deepseek get secret coding-agent-deepseek
kubectl -n coding-agent-deepseek get secret coding-agent-langfuse
```

The deployment pipeline only changes the image field. It does not create or
replace these secrets.

## Manual Deploy

Trigger `.woodpecker/deploy.yml` manually from the Woodpecker UI on `main`.

Optional overrides can be set as Woodpecker environment variables:

```text
IMAGE_REPOSITORY
IMAGE_TAG
K8S_NAMESPACE
K8S_DEPLOYMENT
K8S_CONTAINER
ROLLOUT_TIMEOUT
ENABLE_POD_HEALTH_SMOKE
```

Set `ENABLE_POD_HEALTH_SMOKE=0` if the runner cannot `kubectl exec` into the
pod.
