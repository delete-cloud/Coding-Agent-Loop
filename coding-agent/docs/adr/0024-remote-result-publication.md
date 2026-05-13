# ADR-0024: Publish remote session results as Git-backed review artifacts

**Status**: Proposed
**Date**: 2026-05-13
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0019 made remote Docker workspaces executable through an explicit snapshot
upload and snapshot result download flow. ADR-0020 documented that flow honestly
as a bounded archive round trip that overwrites the local checkout while
preserving `.git`. ADR-0021 added remote operations APIs, workspace archive
manifests, and CLI confirmation before local overwrite. ADR-0022 and ADR-0023
then improved production runtime and phase policy.

That baseline is useful for local-only repos, dogfood smoke tests, and ad hoc
remote execution. It is not the right primary product model for a cloud agent.
A cloud session should treat the remote workspace as the execution site and
publish reviewable results from there. The local checkout should not be the
default destination for applying remote changes.

The current snapshot transfer model also cannot support branch or PR publication
as-is. `create_workspace_archive_base64()` excludes `.git`, and archive
extraction rejects `.git` members. A workspace created only from the current
snapshot archive is therefore a file tree, not a Git checkout with remote,
branch, base SHA, index, and credentials. Any branch or PR workflow must be
Git-backed from the start, or it must explicitly remain a patch/archive export
fallback.

## Decision

Adopt **Remote Result Publication** as the next remote product boundary. A
remote cloud session produces an auditable session result and, when code changes
exist, a reviewable Git-oriented artifact. Local download is an explicit export
mechanism, not the default completion path.

The canonical result contract is:

1. **Session result** — final answer, session status, turn status, workspace id,
   setup/agent phase outcome, commands or verification the agent reported, and
   failure details when available.
2. **Reviewable code result** — changed files summary, added/modified/deleted
   classification, diff stat, binary/large-file markers, and a full patch export
   when the workspace can produce one safely.
3. **Git-backed publication** — for Git-backed workspaces, optional commit,
   branch push, and PR/MR creation, with branch name, commit SHA, remote URL,
   and PR/MR URL returned to the client.
4. **Export fallback** — patch download, archive download, and local archive
   overwrite remain available as explicit fallback paths.

### Workspace source model

Introduce a Git-backed workspace source as the production path for publication:

```json
{
  "workspace_source": {
    "kind": "git",
    "remote_url": "https://github.com/org/repo.git",
    "base_ref": "main",
    "base_sha": "abc123...",
    "runtime_profile": "universal"
  }
}
```

The exact schema may vary during implementation, but the semantics must remain:

- The Git source describes the input repository and publication base, not the
  execution backend. The server may still provision the workspace with the
  configured Docker cloud workspace provider from ADR-0019 through ADR-0023.
- The server clones or checks out a Git repository into the cloud workspace.
- The workspace keeps `.git` and has enough Git metadata to compute diffs,
  commit changes, push a branch, and open a PR when credentials allow it.
- `base_sha` is used when provided to ensure the remote session runs against the
  same committed state the client intended.
- If the local repo has uncommitted changes, untracked changes, or a HEAD that is
  not available from the configured Git remote, the CLI must not silently switch
  to publication mode. It should either fail with a clear message or require an
  explicit snapshot fallback flag.

The existing Docker snapshot source remains supported:

```json
{
  "workspace_source": {
    "kind": "docker",
    "snapshot_archive_base64": "..."
  }
}
```

Snapshot-only sessions may provide a file-tree result summary, patch export when
the server captured a baseline tree, and archive download. They must not claim
branch or PR publication unless the implementation has first created or restored
a real Git checkout with a known remote and base.

### HTTP API surface

Remote result publication is an HTTP-first API, following ADR-0021's rule that
the CLI is a thin client. Add or extend session-scoped operations rather than
putting result semantics only in CLI output:

- `GET /sessions/{session_id}/result`
- `GET /sessions/{session_id}/workspace/diff`
- `GET /sessions/{session_id}/workspace/patch`
- `POST /sessions/{session_id}/publish`

`GET /sessions/{session_id}/result` returns the durable result document for
clients and humans. `GET /workspace/diff` returns structured diff metadata.
`GET /workspace/patch` returns a unified patch or a clear error explaining why a
patch cannot be produced. `POST /publish` accepts an explicit publication mode,
such as `branch` or `pr`, and returns publication metadata.

Publication must be explicit. A remote run may compute and display a result
summary automatically, but it must not push a branch, open a PR, or overwrite a
local checkout unless the user requested that action through flags or a follow-up
operation.

### CLI behavior

Change the recommended remote workflow from "run, then download and overwrite"
to "run, then show remote result and next actions".

For a clean Git repo with a configured remote, the preferred command is:

```bash
coding-agent remote run team --repo . --goal "fix the failing test"
```

The CLI should infer the Git remote, current branch/ref, and HEAD SHA, create a
Git-backed remote workspace, stream the prompt, then print:

- session id;
- final answer/status;
- changed files summary and diff stat;
- verification reported by the agent;
- next commands for patch export, archive download, branch publish, or PR
  publish.

Publishing remains explicit:

```bash
coding-agent remote publish team --session <session-id> --branch
coding-agent remote publish team --session <session-id> --pr
```

Patch and archive export also remain explicit:

```bash
coding-agent remote patch team --session <session-id> > result.patch
coding-agent remote download team --session <session-id> --repo .
```

`remote download` continues to fetch the manifest before archive extraction and
continues to require confirmation unless a documented non-interactive flag is
used. Documentation must describe archive overwrite as a fallback, not the main
cloud-session success path.

### Git credentials and PR creation

Branch publication requires server-side Git credentials scoped to the repository
or provider installation. PR/MR creation requires provider API credentials and a
configured provider integration.

P0 publication configuration should reuse existing dependencies and standard
tools: invoke the Git CLI inside the Git-backed workspace for status, diff,
commit, and push; use `httpx`, which is already a project dependency, for any
provider API calls. Do not add a new Git library or provider SDK unless the
implementation proves that shelling out to Git plus `httpx` is insufficient.

Suggested production configuration shape:

```toml
[remote_publication]
enabled = true
git_author_name = "coding-agent"
git_author_email = "coding-agent@example.com"
allowed_git_hosts = ["github.com"]
git_token_env = "CODING_AGENT_GIT_TOKEN"

[remote_publication.github]
enabled = true
token_env = "CODING_AGENT_GITHUB_TOKEN"
```

The exact field names may change during implementation, but production mode must
fail closed when publication is requested and the required credentials or author
identity are missing.

P0 should implement branch push before PR creation. If PR creation is requested
but provider configuration is missing, the operation should fail clearly or
return branch publication metadata with an instruction to open the PR manually.
It must not silently downgrade a requested PR into local archive download.

Git provider integration should start with GitHub only if needed for the first
implementation. Do not introduce a broad multi-provider abstraction until there
are at least two implemented providers or a concrete integration need.

## Non-goals

- Do not implement bidirectional live sync.
- Do not automatically merge remote results into `main` or any protected branch.
- Do not auto-resolve Git conflicts.
- Do not infer publication mode for dirty local repositories.
- Do not make snapshot archive overwrite the default success path.
- Do not add a broad Git provider abstraction before a second provider exists.
- Do not expand the Docker sandbox or phase security model beyond ADR-0020,
  ADR-0022, and ADR-0023.

## Consequences

- Remote cloud sessions become review-first artifacts rather than local checkout
  mutation workflows.
- Git handles history, review, merge, conflict, rollback, CI, and local pull;
  the coding-agent server avoids inventing a parallel synchronization system.
- The CLI must distinguish clean Git-backed runs from snapshot fallback runs.
  This adds up-front validation but prevents false PR expectations.
- The server must store or derive enough result metadata for non-CLI clients,
  including future Web UI, CI, and editor integrations.
- Existing archive download remains useful for local-only repos and smoke tests,
  but docs and command defaults must no longer present it as the primary remote
  success path.

## Implementation Plan

### PR 1: ADR, schemas, and result API contract

- Add this ADR.
- Extend `src/coding_agent/ui/schemas.py` with response models for session
  result, workspace diff, workspace patch, publication request, and publication
  response.
- Add route stubs or contract tests for:
  - `GET /sessions/{session_id}/result`
  - `GET /sessions/{session_id}/workspace/diff`
  - `GET /sessions/{session_id}/workspace/patch`
  - `POST /sessions/{session_id}/publish`
- Keep unsupported operations fail-fast with explicit status codes and messages.
  Do not fall back to archive download.

### PR 2: Git-backed workspace source

- Add a `GitWorkspaceSourceRequest` schema next to the existing Docker snapshot
  source.
- Extend remote client creation so `remote run --repo .` can detect a clean Git
  checkout, configured origin, current ref, and HEAD SHA.
- Add server provisioning that clones/checks out the requested Git source into
  the Docker workspace while preserving `.git`.
- Require explicit snapshot fallback when the local repo is dirty, unpushed, has
  no remote, or cannot provide a stable base SHA.
- Preserve existing snapshot upload behavior for non-Git and explicit fallback
  workflows.

### PR 3: Diff and patch result generation

- Generate structured changed-file data from the remote Git workspace after a
  turn completes or on demand.
- Report added, modified, deleted, renamed when Git can identify them, binary
  files, and diff stat.
- Add patch export using `git diff` for Git-backed workspaces.
- For snapshot-only workspaces, either produce a patch against a captured
  baseline tree or return a clear unsupported response. Do not mislabel a full
  archive as a patch.

### PR 4: CLI result-first workflow

- Change `remote run` so the recommended path prints session result and changed
  files instead of automatically downloading and overwriting the local checkout.
- Add explicit commands or flags for patch export, branch publish, PR publish,
  and archive download.
- Keep `remote download` confirmation and manifest behavior from ADR-0021.
- Update `docs/remote-sandbox-production.md` so archive overwrite is documented
  as fallback and Git-backed result publication is documented as the production
  path.

### PR 5: Branch publication

- Implement `POST /sessions/{session_id}/publish` with `mode = "branch"` for
  Git-backed workspaces.
- Add publication config loading in `src/coding_agent/ui/http_server.py` for Git
  author identity and Git push credentials. Missing required publication config
  must fail the publish request, not remote session creation.
- Create a deterministic branch name such as
  `coding-agent/session-<session-id>` unless the request provides an allowed
  branch name.
- Commit remote workspace changes with a generated message that includes the
  session id.
- Push the branch to the configured remote using server-side credentials.
- Return branch name, commit SHA, remote URL, and pushed ref.

### PR 6: GitHub PR publication

- Add optional GitHub PR creation when GitHub credentials and repository mapping
  are configured.
- Use the existing `httpx` dependency for GitHub API calls unless a later ADR
  accepts a provider SDK.
- Return PR URL and branch publication metadata.
- If PR creation fails after a branch was pushed, report the branch metadata and
  the PR error; do not hide the partial publication state.

## Alternatives Rejected

- Keep improving local archive overwrite as the primary path — rejected because
  it makes the product a remote execution accelerator for a local checkout,
  increases data-loss risk, and duplicates problems Git already solves.
- Add live sync before publication — rejected because live sync brings conflict,
  rename, ignore-rule, concurrent edit, and trust-policy complexity before the
  simpler review-first result model exists.
- Push branches from snapshot-only workspaces — rejected because current
  snapshots intentionally exclude `.git`; branch publication needs a real Git
  checkout, a known base, and credentials.
- Open PRs automatically at the end of every run — rejected because publication
  mutates shared remote state and must be explicit.
- Build a full GitHub/GitLab/Bitbucket abstraction immediately — rejected
  because branch push plus one provider-specific PR implementation is a smaller
  correct step until multiple providers are real.
- Remove archive download — rejected because local-only repos, no-remote repos,
  smoke tests, and operational recovery still need explicit export fallback.

## Acceptance Criteria

- [ ] `test_create_session_accepts_git_workspace_source_with_base_sha`
- [ ] `test_remote_run_clean_git_repo_uses_git_workspace_source`
- [ ] `test_remote_run_dirty_git_repo_requires_explicit_snapshot_fallback`
- [ ] `test_session_result_returns_status_answer_workspace_and_verification_summary`
- [ ] `test_workspace_diff_reports_added_modified_deleted_and_binary_files`
- [ ] `test_workspace_patch_returns_unified_diff_for_git_workspace`
- [ ] `test_workspace_patch_rejects_snapshot_without_baseline_with_clear_error`
- [ ] `test_remote_run_prints_result_summary_without_downloading_by_default`
- [ ] `test_remote_download_keeps_manifest_confirmation_for_archive_overwrite`
- [ ] `test_publish_branch_commits_and_pushes_git_workspace_changes`
- [ ] `test_publish_pr_returns_branch_metadata_when_pr_provider_is_not_configured`
- [ ] `uv run pytest tests/ui/test_http_server.py tests/ui/test_http_server_workspace_transfer.py tests/cli/test_remote_client.py tests/coding_agent/environment/ -k "git_workspace or session_result or workspace_diff or workspace_patch or publish or remote_run" -v`
- [ ] `uv run basedpyright --level error src/coding_agent/ui/schemas.py src/coding_agent/ui/http_server.py src/coding_agent/remote/client.py src/coding_agent/__main__.py src/coding_agent/environment/docker_workspace_provider.py`
- [ ] `uv run ruff format --check src/coding_agent/ui/schemas.py src/coding_agent/ui/http_server.py src/coding_agent/remote/client.py src/coding_agent/__main__.py src/coding_agent/environment/docker_workspace_provider.py tests/ui/test_http_server.py tests/ui/test_http_server_workspace_transfer.py tests/cli/test_remote_client.py tests/coding_agent/environment/`

## References

- `docs/adr/0017-cloud-workspace-execution.md`
- `docs/adr/0019-remote-client-cloud-workspace-deployment.md`
- `docs/adr/0020-team-production-docker-remote-sandbox-baseline.md`
- `docs/adr/0021-remote-session-and-workspace-operations-api.md`
- `docs/adr/0022-runtime-profiles-and-sandbox-policy.md`
- `docs/adr/0023-setup-and-agent-phase-separation.md`
- `docs/remote-sandbox-production.md`
- `src/coding_agent/workspace_archive.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/remote/client.py`
- `src/coding_agent/__main__.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_http_server_workspace_transfer.py`
- `tests/cli/test_remote_client.py`
- `tests/coding_agent/environment/test_workspace_archive.py`
