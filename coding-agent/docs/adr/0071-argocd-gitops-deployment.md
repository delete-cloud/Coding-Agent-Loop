# ADR-0071: ArgoCD GitOps deployment for the o6n agent

**Status**: Proposed
**Date**: 2026-06-16

## Context

Image builds are already automated: a push to the Forgejo mirror triggers a
Woodpecker pipeline that builds and pushes `git.mesh.kinaz.me/kina/coding-agent`
tagged `:<sha>` and `:main`. Deployment is not automated. `.woodpecker/deploy.yml`
is a `manual`-event pipeline defaulting to `HELM_DEPLOY_MODE: dry-run`, and in
practice rollouts have been done by hand over SSH (`helm upgrade` on the node).

Manual deployment is slow and error-prone, and it lacks drift detection, a
deploy history, a one-click rollback, and a GUI. The most recent manual rollout
(REVISION 6) also appeared to drop live sessions; the real cause was the
destructive idle-GC sweep the restart triggered, plus the lack of a pre-deploy
in-flight drain (see ADR-0070).

A complication: the real o6n values (NodePort, mesh topology, secret names) were
deliberately scrubbed from the public GitHub repo (PR #600); the chart ships a
placeholder `values-example.yaml` and the deploy script fails closed on
placeholder content. Any GitOps source of truth must therefore live in a
**private** repository, not public GitHub.

## Decision

Adopt **ArgoCD** (pull-based GitOps) as the target deployment mechanism for the
o6n agent, replacing manual SSH `helm upgrade`.

- **Source of truth.** ArgoCD watches a private Git repository (e.g. Forgejo
  `sre-infra/deploy/coding-agent`) holding the Helm chart reference and the real
  o6n values. The chart itself stays in GitHub/Forgejo; values never enter the
  public repo.
- **Image promotion.** ArgoCD Image Updater watches the registry and promotes
  **immutable `:<sha>` tags** (never the floating `:main`), writing the new tag
  back to the private Git repo for reproducibility and clean rollback.
- **Sync policy.** Manual sync (not auto-sync), surfacing an ArgoCD diff/preview
  before apply. Deploys stay intentional, which also bounds when the pod rolls
  and (until ADR-0070 D1/D2 land) when in-flight turns are interrupted.
- **Adoption.** The existing Helm release was adopted with `helm upgrade
  --take-ownership`; ArgoCD adopts the same release (Helm-source Application),
  no second take-ownership needed.
- **Secrets.** Never in Git. Kubernetes Secrets / SealedSecrets referenced by
  name from values, as today.
- **Environments.** Separate values per environment (local orbstack vs o6n prod).

### Deployment contract (binding for any deploy path)

1. Source of truth: chart in GitHub/Forgejo; **values in the private repo only**.
2. Image tag policy: deploy pins an immutable `:<sha>`; promotion is explicit.
3. Trigger: manual sync with a diff/preview before apply.
4. Pre-deploy safety: relies on ADR-0070 D1 (idle GC must not delete durable
   sessions) + D2 (graceful drain); until they land, deploys are intentional and
   may interrupt in-flight turns.
5. Rollback: ArgoCD rollback (or `helm rollback`) to the previous revision.
6. Secrets: never in Git; k8s Secret / SealedSecret by name.
7. Environment isolation: distinct values for orbstack-local vs o6n-prod.

## Alternatives Rejected

- **Woodpecker CD (push-based), extending `.woodpecker/deploy.yml`.** Lowest
  friction (reuses existing pipeline, kubeconfig, and `deploy_values` secret),
  and the `manual` event already gives a one-click trigger in the Woodpecker UI.
  Rejected as the *target* because it has no drift detection or self-heal,
  rollback is a pipeline re-run, the GUI is weaker, and the source of truth is a
  CI secret rather than auditable Git history. It remains a reasonable interim if
  ArgoCD standup is deferred.
- **Keep manual SSH `helm upgrade`.** Rejected: no audit trail, no drift
  detection, no one-click rollback, and it caused the ADR-0070 session-loss
  incident.
- **ArgoCD auto-sync (self-healing on every Git change).** Rejected for a single
  prod agent: deploys should be intentional, and auto-sync would roll the pod —
  and (pre-ADR-0070-D1/D2) interrupt in-flight turns — on every values commit.
- **Floating `:main` image tag.** Rejected: not reproducible and defeats clean
  rollback; pin immutable `:<sha>`.
- **Put real o6n values in the public GitHub repo.** Rejected: violates the #600
  topology/secret boundary. Private repo only.

## Acceptance Criteria

Deployment infrastructure; criteria are operational checks plus the existing
chart/deploy guards that must keep passing.

- [ ] Operational: an ArgoCD `Application` syncs the existing Helm release in
  `coding-agent-deepseek` to Synced/Healthy from the private values repo.
- [ ] Operational: Image Updater promotes a new `:<sha>` by committing to the
  private repo; ArgoCD sync rolls the Deployment to that exact tag.
- [ ] Operational: ArgoCD rollback to the previous revision restores the prior
  image and config.
- [ ] `test_deploy_script_apply_mode_rejects_placeholder_image_repository` and
  `test_deploy_script_apply_mode_rejects_example_values_without_content` keep
  passing (no public-repo values regression, PR #600).
- [ ] `test_helm_chart_lints` and the chart render contract tests keep passing.
- [ ] `uv run pytest tests/deploy/test_deploy_script.py tests/deploy/test_helm_chart.py -q`

## References

- `docs/adr/0070-restart-safe-live-sessions.md` (D1 idle-GC retention fix + D2
  graceful drain are the prerequisites for comfortable GitOps deploys)
- `docs/deployment/phase2-k8s.md`
- `docs/deployment/woodpecker.md`
- `.woodpecker/ci.yml`, `.woodpecker/deploy.yml`
- `helm/` (chart; `values-example.yaml` placeholder per PR #600)
- `tests/deploy/test_deploy_script.py`, `tests/deploy/test_helm_chart.py`
- `https://argo-cd.readthedocs.io/`
- `https://argocd-image-updater.readthedocs.io/`
