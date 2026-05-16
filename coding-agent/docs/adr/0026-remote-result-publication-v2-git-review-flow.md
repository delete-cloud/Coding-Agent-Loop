# ADR-0026: Harden remote result publication as the Git review flow

**Status**: Accepted / implemented through PRs #174-#177
**Date**: 2026-05-15
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0024 changed the remote workspace product model from local snapshot
overwrite to remote result publication. It added Git-backed workspace sources,
session result, workspace diff, workspace patch, branch publication, GitHub PR
publication, and result-first CLI output. ADR-0025 then made session/workspace
metadata durable so publication refs and workspace lifecycle can be inspected
after runtime shutdown and server restart.

Dogfood of ADR-0024 and ADR-0025 confirmed the direction: a remote cloud agent
should not make "download the result back into the local checkout" the normal
success path. The remote workspace is the execution site. The result should be
reviewed and applied through remote Git artifacts: diff, patch, branch, and PR.
The local machine should normally consume the result later through ordinary Git
operations such as reviewing a PR, checking out a branch, or pulling after merge.

The current ADR-0024 implementation is a working baseline, but it still needs a
production-quality review flow. In particular, review artifacts must faithfully
represent what publication will commit, publication failures must not destroy
the ability to inspect/retry results, and server-side Git clone/publish
operations must fail closed before using untrusted transports or hosts.

This ADR strengthens ADR-0024. It does not replace ADR-0024's API names or move
remote operations into a new product shape.

## Decision

Adopt **Git Review Flow v2** for remote result publication:

1. **Remote Git artifacts are the primary result path.** A completed remote
   coding session should expose result, diff, patch, branch, and PR artifacts
   from the remote workspace. Local archive download remains an explicit export
   fallback.
2. **Review artifacts and publication must be content-consistent.** The diff and
   patch a user reviews must describe the same file set that branch publication
   would commit.
3. **Publication must be retry-safe and inspectable.** Validation failures,
   credential failures, host allowlist failures, and provider API failures must
   not leave the workspace in a state where diff/patch disappear without an
   explicit publication state explaining what happened.
4. **Server-side Git operations must be allowlisted before I/O.** Git source
   clone and publication remotes must validate scheme, host, URL shape, and
   credential policy before running `git clone`, `git fetch`, `git push`, or
   provider API calls.
5. **CLI remains a thin client.** The HTTP API is the product contract; CLI
   commands only call the API, stream events, and display next actions.

The default user story is:

```text
remote run against a clean Git-backed source
→ inspect session result / diff / patch
→ explicitly publish branch or PR
→ review/merge through Git provider
→ local checkout receives changes later through normal Git workflows
```

`remote download` and archive overwrite remain available for local-only repos,
smoke tests, and operational recovery. They must be documented and displayed as
fallback export commands, not as the default cloud-agent result application
path.

### Diff, patch, and publish consistency

For Git-backed workspaces, diff and patch generation must include all changes
that branch publication would commit:

- tracked modifications;
- deletions;
- untracked added files;
- binary file markers;
- renames when Git can detect them.

Implementations should not use plain `git diff HEAD --` as the final source for
review artifacts because it omits untracked files. The preferred mechanism is a
temporary index:

1. copy or create an isolated `GIT_INDEX_FILE`;
2. run `git add -A` against that temporary index;
3. produce summary with `git diff --cached --name-status --numstat HEAD --`;
4. produce patch with `git diff --cached --binary HEAD --`;
5. leave the real workspace index and working tree untouched.

Branch publication may still use the real index when committing, but its commit
input must match the same "add all current workspace changes" semantics used by
the reviewed diff/patch.

### Publication mutation ordering

Publication must perform all validations that can be done without mutating the
workspace before any `git add`, `git commit`, branch checkout, or push:

- requested mode is supported;
- branch name is valid and allowed;
- remote URL exists and is valid;
- remote scheme and host are allowlisted;
- remote URL contains no query/fragment credential material unless explicitly
  supported;
- publication is enabled;
- Git author identity is configured;
- required token environment variables exist and are non-empty;
- PR provider configuration exists when strict PR publication is requested.

If validation fails, the workspace must remain inspectable through result,
diff, and patch APIs.

If mutation succeeds locally but a later push or PR API call fails, the response
must report explicit partial state, such as local branch name, local commit SHA,
push status, and PR error. The system must not pretend nothing happened, and it
must not turn the result into an empty diff without exposing the local
publication state.

### Git source clone safety

Git-backed workspace creation must validate client-provided source URLs before
server-side clone/fetch operations:

- allow only configured schemes, initially `https`;
- require a hostname;
- require host membership in `[remote_sources.git].allowed_hosts`;
- reject `file://`, `ssh://`, scp-like `git@host:repo`, relative paths, and
  local filesystem paths unless a later ADR explicitly enables them;
- reject username/password, query, and fragment in source URLs unless a later
  ADR defines a redaction and credential policy.

This is separate from publication remote validation. Source clone allowlists
control where the server may read from; publication allowlists control where it
may write to.

### Publication response and durable metadata

Publication responses and ADR-0025 result refs should be rich enough for humans,
CI, Web UI, and future editor integrations:

- session id and workspace id;
- mode: `branch` or `pr`;
- status: `published`, `partial`, or `failed`;
- base ref and base SHA when known;
- branch name;
- commit SHA when a local commit exists;
- pushed ref when push succeeds;
- redacted remote URL without query or fragment;
- PR URL when PR creation succeeds;
- provider error summary when PR creation fails;
- retry guidance when the operation is retryable.

Cleaned workspace records may retain publication refs after workspace files are
gone. APIs must clearly distinguish durable metadata from provider-local
exports that require a still-existing workspace.

### Layering decision

ADR-0026 keeps Git review flow v2 in `coding_agent`.

The implementation may shape result and publication metadata so it can later be
backed by an `agentkit` session-result/artifact-ref contract, but this ADR does
not migrate runtime storage, workspace retention, Git publication, or remote CLI
behavior into `agentkit`.

Future downshift candidates for a separate ADR include:

- `SessionResult` / `TurnResult`;
- `VerificationSummary` / `FailureSummary`;
- `ArtifactRef` / `ResultRef`;
- result reducers over `agentkit.tape.extract.TurnTrace`;
- provider-neutral metadata-only artifact store protocols.

The following must remain in `coding_agent` for this ADR:

- Git-backed workspace source;
- Git clone/diff/patch/branch publication execution;
- GitHub PR integration;
- Docker workspace provider behavior;
- remote workspace retention, `provider_instance_id`, pin/unpin, and GC;
- HTTP remote operations and CLI commands.

This avoids turning Git Review Flow v2 into an `agentkit` refactor. A later
ADR-0027 may define `agentkit` session result and artifact reference models.

## Non-goals

- Do not implement live sync or background bidirectional sync.
- Do not automatically apply remote changes to the local checkout.
- Do not automatically merge remote changes into protected branches.
- Do not resolve Git conflicts.
- Do not add a broad Git provider abstraction before a second provider is real.
- Do not move Docker workspace, Git publication, workspace retention, or remote
  CLI behavior into `agentkit`.
- Do not store workspace files, Git checkouts, or archive blobs in PostgreSQL.
- Do not remove archive download fallback.

## Consequences

- The product boundary remains cloud-agent-like: review remote results remotely,
  then consume them through Git.
- Git remains responsible for review, merge, rollback, CI, and local checkout
  update; coding-agent avoids inventing a competing sync system.
- Diff/patch generation becomes slightly more complex because it must model
  "what publish would commit" without mutating the workspace.
- Publication code must distinguish preflight validation failures from partial
  publication failures.
- Future `agentkit` extraction stays possible because result refs and artifact
  metadata are shaped as provider-neutral references, but that migration is not
  on the critical path for Git Review Flow v2.

## Implementation Plan

### PR 1: ADR and tests for review/publish consistency

- Add this ADR.
- Add regression tests in
  `tests/coding_agent/environment/test_docker_workspace_provider.py` proving:
  - untracked text files appear in workspace diff;
  - untracked binary files appear as added binary files;
  - patch export contains new text file content;
  - diff/patch generation does not mutate the real Git index.

### PR 2: Temporary-index diff and patch generation

- Update `src/coding_agent/environment/docker_workspace_provider.py` so
  Git-backed workspace diff and patch use a temporary index with `git add -A`.
- Preserve existing provider method signatures in
  `src/coding_agent/environment/workspace_provider.py`.
- Keep snapshot-only behavior explicit; do not pretend archive export is a
  patch.

### PR 3: Git source clone URL allowlist

- Add pre-clone validation for `workspace_source.kind = "git"` in Docker
  workspace provisioning.
- Use `[remote_sources.git].allowed_hosts` as the source clone host allowlist.
- Reject local paths, `file://`, SSH URLs, scp-like syntax, query strings,
  fragments, and username/password in source URLs.
- Add tests proving disallowed source URLs fail before `subprocess.run`.

### PR 4: Publication preflight and retry-safe mutation

- Move branch name, remote URL, host allowlist, author identity, and token env
  checks before `git add`, `git commit`, branch checkout, or push.
- Add tests proving unsafe remote hosts and missing token env do not mutate the
  workspace into an empty diff.
- If local commit succeeds but push/PR fails, return explicit partial
  publication metadata instead of hiding the local state.

### PR 5: Response metadata and durable refs

- Extend `src/coding_agent/ui/schemas.py` publication response models with
  publication status, base ref/SHA when known, redacted remote URL, partial
  state, and retry guidance.
- Persist the richer publication metadata through ADR-0025 workspace result refs.
- Ensure redacted remote URLs drop query and fragment.

### PR 6: CLI and documentation polish

- Update `src/coding_agent/__main__.py` output so remote run and publish
  messages reinforce Git review flow as the main path and archive download as
  fallback.
- Update `docs/remote-sandbox-production.md` with Git Review Flow v2,
  preflight/partial publication semantics, and local download fallback wording.

## Alternatives Rejected

- **Make local safe sync the next milestone** — rejected because the product
  direction is remote review and Git consumption, not local checkout mutation.
- **Move result/publication into `agentkit` immediately** — rejected because it
  would mix a product hardening milestone with a runtime abstraction refactor.
- **Use plain `git diff HEAD --` for review artifacts** — rejected because it
  omits untracked files and can make review output differ from publish output.
- **Commit before validating publication config** — rejected because failed
  preflight should not mutate workspace state or erase inspectable diff/patch.
- **Allow arbitrary Git source URLs** — rejected because server-side clone is
  network and filesystem access initiated by user input.
- **Remove archive download** — rejected because snapshot fallback, local-only
  repos, dogfood smoke, and recovery workflows still need explicit export.

## Implementation Record

Implemented through:

- **#174** — added this ADR and fixed the Git Review Flow v2 decision boundary.
- **#175** — returned explicit `partial` branch publication state when local
  commit succeeds but push fails; surfaced that state through HTTP, durable
  workspace result refs, and CLI output.
- **#176** — rejected publication remotes with username/password, query, or
  fragment before mutating the workspace.
- **#177** — added real Git coverage for untracked binary files in workspace
  diff output.

Earlier ADR-0024 follow-up PRs had already implemented the temporary-index
diff/patch path, Git-backed workspace source validation, remote result/diff/
patch/publish API surface, CLI commands, and documentation updates that this
ADR hardens.

## Acceptance Criteria

- [x] Untracked text files are included in workspace diff.
  Covered by
  `test_docker_workspace_provider_diff_and_patch_include_untracked_files`.
- [x] Untracked text files are included in workspace patch.
  Covered by
  `test_docker_workspace_provider_diff_and_patch_include_untracked_files`.
- [x] Untracked binary files are reported as added binary files.
  Covered by
  `test_docker_workspace_provider_diff_marks_untracked_binary_file`.
- [x] Diff/patch generation does not mutate the real Git index.
  Covered by
  `test_docker_workspace_provider_diff_and_patch_include_untracked_files` and
  `test_docker_workspace_provider_diff_marks_untracked_binary_file`.
- [x] Disallowed Git source clone hosts fail before subprocess execution.
  Covered by
  `test_docker_workspace_provider_rejects_unallowlisted_git_source_before_clone`.
- [x] Unsafe Git source transports fail before subprocess execution.
  Covered by
  `test_docker_workspace_provider_rejects_unsafe_git_source_transport_before_clone`.
- [x] Missing publication token preflight fails before commit.
  Covered by `test_docker_workspace_provider_requires_git_token_before_commit`.
- [x] Disallowed publication remotes fail before commit.
  Covered by
  `test_docker_workspace_provider_rejects_unsafe_git_remote_before_push`,
  `test_docker_workspace_provider_rejects_sensitive_git_remote_url_before_commit`,
  and
  `test_docker_workspace_provider_rejects_unallowlisted_git_remote_host_before_push`.
- [x] Push failure returns explicit partial local commit state.
  Covered by
  `test_docker_workspace_provider_returns_partial_publication_when_push_fails`,
  `test_publish_branch_returns_partial_state_when_push_fails`,
  `test_remote_publish_branch_prints_partial_publication_result`, and
  `test_remote_publish_pr_reports_branch_when_branch_push_is_partial`.
- [x] Publication remote URLs do not leak query or fragment in responses.
  Sensitive query/fragment publication remotes are now rejected before mutation
  by `test_docker_workspace_provider_rejects_sensitive_git_remote_url_before_commit`.
- [x] Focused regression suite passes:
  `uv run pytest tests/ui/test_http_server.py tests/cli/test_remote_client.py tests/coding_agent/environment/test_docker_workspace_provider.py -k "git_workspace or workspace_diff or workspace_patch or publish" -q`.
- [x] Type check passes:
  `uv run basedpyright --level error src/coding_agent/ui src/coding_agent/environment src/coding_agent/remote/client.py src/coding_agent/__main__.py`.
- [x] Touched-file format checks pass during the implementation PRs. A full
  tree format check currently reports pre-existing formatting drift in files
  outside ADR-0026's touched set; that drift is not part of this ADR.

## References

- `docs/adr/0021-remote-session-and-workspace-operations-api.md`
- `docs/adr/0024-remote-result-publication.md`
- `docs/adr/0025-durable-remote-session-and-workspace-retention.md`
- `docs/remote-sandbox-production.md`
- `src/agentkit/tape/extract.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/remote/client.py`
- `src/coding_agent/__main__.py`
- `tests/ui/test_http_server.py`
- `tests/cli/test_remote_client.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
