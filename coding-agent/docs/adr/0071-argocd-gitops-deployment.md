# ADR-0071: ArgoCD GitOps deployment for the o6n agent

**Status**: Accepted
**Date**: 2026-06-16

## Context

Image builds are already automated: a push to the Forgejo mirror triggers a
Woodpecker pipeline that builds and pushes `git.mesh.kinaz.me/kina/coding-agent`
tagged `:<sha>` and `:main`. Deployment is not automated. `.woodpecker/deploy.yml`
is a `manual`-event pipeline defaulting to `HELM_DEPLOY_MODE: dry-run`, and in
practice rollouts have been done by hand over SSH (`helm upgrade` on the node).

Manual deployment is slow and error-prone, and it lacks drift detection, a
deploy history, a one-click rollback, and a GUI. The recent REVISION 6 rollout
surfaced two ADR-0070 issues that are independent of how we deploy: the durable
idle-GC deletion (ADR-0070 D1, the actual cause of the lost session rows) and the
absence of a pre-deploy in-flight-turn drain (ADR-0070 D2, a deploy-safety gap,
not a row-loss cause).

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
  detection, no one-click rollback. (The ad-hoc REVISION 6 rollout *surfaced* the
  ADR-0070 idle-GC deletion, but that deletion bug is independent of the deploy
  method.)
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

Reconciled 2026-07-02. The `Application` and sync policy are owned by the
private infra repo (`sre-infra:infra/k8s/o6n/argocd/apps/o6n-coding-agent.yaml`).
Two landed details differ from the text below as written in June:
promotion uses explicit `deploy(o6n): bump coding-agent image` commits pinning
an exact `:<sha>` (not the argocd-image-updater component), and the "manual
sync" clause has since graduated to `syncPolicy.automated` (prune/selfHeal off)
per the planned manual→auto graduation — the auto-sync rejection above was
explicitly conditioned on ADR-0070 D1/D2 not having landed, and both have
landed. The sync-policy decision now lives with the infra repo, not this ADR.

- [x] Operational: the ArgoCD `Application` `o6n-coding-agent` syncs the
  existing Helm release in the o6n-prod namespace (`coding-agent-deepseek` — an
  environment-specific override; the chart/deploy-script default is
  `coding-agent`) to Synced/Healthy from the private values repo (verified live
  2026-06-17).
- [x] Operational: a new `:<sha>` is promoted by committing an image-tag bump
  to the private repo; ArgoCD sync rolls the Deployment to that exact tag
  (bump commits in continuous use since 2026-06).
- [x] Operational: rollback to the previous revision restores the prior image
  and config by reverting the bump commit in the private repo (Git is the
  source of truth).
- [x] `test_deploy_script_apply_mode_rejects_placeholder_image_repository` and
  `test_deploy_script_apply_mode_rejects_example_values_without_content` keep
  passing (no public-repo values regression, PR #600).
- [x] `test_helm_chart_lints` and the chart render contract tests keep passing.
- [x] Existing deploy-script and helm-chart guard tests pass: `uv run pytest tests/deploy/test_deploy_script.py tests/deploy/test_helm_chart.py -q` (60 passed, 2026-07-02)

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
