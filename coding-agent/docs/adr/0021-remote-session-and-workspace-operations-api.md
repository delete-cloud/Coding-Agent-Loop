# ADR-0021: Define remote session and workspace operations API

**Status**: Proposed
**Date**: 2026-05-10
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0017 established cloud workspace execution behind the environment boundary.
ADR-0019 made Docker-backed remote sessions executable from the CLI, and
ADR-0020 raised that Docker path to a controlled team production baseline with
explicit production configuration, authentication, quota, GC, health/readiness,
and honest snapshot round-trip documentation.

That P0 baseline is enough for a team to deploy and try remote Docker
workspaces on a controlled host. It is not enough for daily operations. Teams
still need to inspect session and workspace state, cancel a stuck remote turn,
download results safely, manually clean stale workspaces, observe cleanup
failures, and build non-CLI clients such as Web UI, CI, dashboards, and editor
extensions. Treating these needs as "CLI UX" would put product semantics in one
terminal client and force later clients to reverse-engineer behavior from CLI
flags and output.

The next stage therefore needs an HTTP-first remote operations surface. The CLI
should become a thin client over that surface. State, authorization, lifecycle,
event streaming, cancellation, cleanup, and archive download contracts belong in
the server API.

## Decision

Define P1 as **Remote Session and Workspace Operations API**. The HTTP API is
the product boundary for remote operations. The CLI may add or rename commands,
but those commands must call the HTTP operations API rather than owning core
state or lifecycle semantics.

P1 keeps the current Docker provider and snapshot transfer model. It does not
add live sync, patch export, Kubernetes, SSH, VM, microVM providers, full RBAC,
multi-tenant identity, a Web UI, metrics dashboards, or a complete interactive
TUI.

### Session operations

The canonical P1 session API preserves the current session routes and adds the
missing operations explicitly.

New in P1:

- `GET /sessions`
- `POST /sessions/{session_id}/cancel`
- `GET /sessions/{session_id}/workspace/archive/manifest`
- `GET /sessions/{session_id}/workspace/archive`

Existing preserved endpoints:

- `POST /sessions`
- `POST /sessions/{session_id}/prompt`
- `POST /sessions/{session_id}/approve`
- `GET /sessions/{session_id}/events`
- `GET /sessions/{session_id}`
- `DELETE /sessions/{session_id}`
- `GET /sessions/{session_id}/workspace`

`GET /sessions/{session_id}/workspace` is a compatibility alias for archive
download. New clients should use `/workspace/archive/manifest` before
`/workspace/archive`.

Session responses must expose enough state for humans and clients to understand
remote work without reading server logs:

- `session_id`
- `status`
- `turn_status`
- `created_at`
- `updated_at`
- `origin`
- `execution_binding.kind`
- `workspace_id` when cloud-bound
- `goal` or last prompt summary when available
- provider/model metadata already tracked by the session

Session status and turn status are separate:

- Session status: `created`, `running`, `waiting_approval`, `completed`,
  `failed`, `closed`
- Turn status: `idle`, `running`, `cancelling`, `cancelled`, `failed`

`POST /sessions/{session_id}/cancel` is asynchronous. It returns an accepted
state such as:

```json
{
  "session_id": "sess-123",
  "turn_id": "turn-456",
  "status": "cancelling"
}
```

The server must not pretend that cancellation finished synchronously. Docker
exec, tool execution, and the agent loop may need a short cleanup window. The
session status endpoint and event stream report the eventual `cancelled` or
`failed` state.

### Workspace operations

The canonical P1 workspace API is:

- `GET /workspaces`
- `GET /workspaces/{workspace_id}`
- `DELETE /workspaces/{workspace_id}`
- `POST /workspaces/gc`
- `GET /workspaces/{workspace_id}/archive/manifest`
- `GET /workspaces/{workspace_id}/archive`

Workspace status is separate from session status:

- `active`
- `stale`
- `cleaning`
- `cleaned`
- `cleanup_failed`

The workspace API is operational, not a second execution path. It must use the
same `WorkspaceProvider` ownership rules, workspace id validation, active
session exclusion, and cleanup paths introduced by ADR-0019 and ADR-0020. It
must not delete arbitrary directories or containers that do not match the Docker
provider workspace id policy.

Normal users should prefer session-scoped archive endpoints. Workspace-scoped
archive and cleanup endpoints are administrative operations unless the server can
prove the workspace belongs to the caller's session.

### Archive manifest and safe download

P1 keeps snapshot round-trip and does not implement patch export. It does add a
manifest step before download so clients can make local overwrite behavior
explicit.

The manifest response must include:

- `workspace_id`
- `session_id` when known
- `format`
- `generated_at`
- `file_count`
- `total_bytes`
- `changed_files`
- `deleted_files`
- `excluded_files`
- `archive_sha256` when the archive is already materialized or when
  `total_bytes < 100MB` and the digest can be computed during the same archive
  pass

Implementations must document which `archive_sha256` rule they follow. If the
archive is not materialized and `total_bytes >= 100MB`, the field may be omitted
to avoid a second export or full archive read.

The CLI must fetch the manifest before downloading an archive into a local repo
and must clearly state that the local working tree will be overwritten while
`.git` is preserved. P1 can keep the current full archive overwrite behavior,
but the user-facing contract must be explicit and confirmed unless a documented
non-interactive flag is used.

### Events and attach

`GET /sessions/{session_id}/events` remains the HTTP attach/monitor primitive.
Existing SSE event names based on wire message classes remain compatible in P1.
New lifecycle events may be added for cancellation, session close, and workspace
cleanup, but clients must not rely on CLI-only loops for attach semantics.

P1 does not require a complete interactive TUI. It requires a stable HTTP event
surface that `coding-agent attach`, future Web UI, CI, and editor clients can
consume.

### Authorization scope

P1 adds lightweight token scope, not full RBAC.

Production servers may configure:

```toml
[server]
bearer_token_env = "CODING_AGENT_BEARER_TOKEN"
admin_bearer_token_env = "CODING_AGENT_ADMIN_BEARER_TOKEN"
```

The normal bearer token can:

- create sessions
- inspect sessions it owns
- send prompts to sessions it owns
- cancel turns for sessions it owns
- approve tool requests for sessions it owns
- download archives for workspaces bound to sessions it owns
- close sessions it owns

The admin bearer token can:

- list all sessions
- inspect all sessions
- list all workspaces
- inspect workspaces
- trigger global GC
- clean up stale or failed workspaces
- download workspace archives for operational recovery

If the current implementation does not yet have user identity, P1 derives a
stable owner label from the authenticated token:

1. Compute the SHA-256 hex digest of the full authenticated token.
2. Consult a persistent `TokenLabelMap` keyed by token digest. The map may be
   configuration-backed for static deployments or database-backed for future
   dynamic token stores.
3. If the digest has an explicit label in `TokenLabelMap`, use that label.
4. Otherwise use `owner:<sha256-hex>` as the default derived label.
5. If a label collision is detected for two different token digests, persist a
   deterministic disambiguated label such as `owner:<sha256-hex>:<counter>`.
   Config-only deployments that cannot persist the disambiguation must fail fast
   and require an explicit `TokenLabelMap` entry. An implementation may instead
   use HMAC-SHA256 with a per-deployment salt stored in configuration, but it
   must document that choice and must not generate a new salt at startup.

The system must consult `TokenLabelMap` whenever resolving owner labels. Do not
silently expose admin operations to the normal token merely because there is only
one configured token.

Development mode may keep authentication disabled for local demos as permitted
by ADR-0020. Production mode must fail closed if required tokens are missing.

### CLI as thin client

The CLI follows the HTTP API:

- `coding-agent remote sessions list <remote>`
- `coding-agent remote sessions status <remote> <session-id>`
- `coding-agent remote sessions close <remote> <session-id>`
- `coding-agent remote sessions cancel <remote> <session-id>`
- `coding-agent remote workspaces list <remote>`
- `coding-agent remote workspaces cleanup <remote> --stale`
- `coding-agent remote workspaces rm <remote> <workspace-id>`
- `coding-agent remote run <remote> --repo . --goal "..."`
- `coding-agent remote download <remote> --session <session-id>`
- `coding-agent remote attach <remote> --session <session-id>`

`remote repl` remains as a compatibility alias for one-shot remote run, but
documentation and help should recommend `remote run` for new workflows.

## Consequences

- Remote operations become automatable by any HTTP client, not just the Python
  CLI.
- Session state, workspace state, cancellation, archive download, and manual
  cleanup gain explicit contracts that future UI and CI clients can share.
- The server must track more state than P0 exposed. Some status fields may be
  derived initially, but the public schema must be stable enough for clients.
- Lightweight token scope prevents admin operations from leaking into normal
  user flows, but it is not a substitute for full multi-user identity or RBAC.
- Archive manifests improve overwrite safety without committing P1 to live sync
  or patch export.
- Existing SSE clients continue to work because P1 does not rename current wire
  event names without a versioned migration.

## Implementation Plan

Before implementing P1 API changes, keep the existing integration baseline green
by restoring the missing parent/child subagent evaluation golden fixtures and
running the focused integration regression that previously failed.

### PR 1: Operation schemas, scoped auth, and session list/status

- Extend `src/coding_agent/ui/auth.py` to return an auth context with token
  scope and stable owner label instead of only returning a raw token string.
- Preserve backward compatibility for existing `verify_api_key` call sites while
  adding dependencies for normal and admin operations.
- Extend `src/coding_agent/ui/schemas.py` with session summary/status response
  models.
- Extend `src/coding_agent/ui/session_manager.py` with public methods for
  listing session summaries and deriving `session_status` / `turn_status`.
- Add `GET /sessions`.
- Expand `GET /sessions/{session_id}` to include execution binding, workspace id,
  status, turn status, origin, and runtime metadata.
- Add tests in `tests/ui/test_http_server.py` for normal-token visibility,
  admin-token visibility, missing-token rejection in production config, and
  session status fields.

### PR 2: Cancel operation and event contract hardening

- Add `POST /sessions/{session_id}/cancel`.
- Cancellation must set `turn_status = "cancelling"` before requesting task
  cancellation and must report eventual `cancelled` or `failed`.
- Reuse existing runtime task cancellation paths where possible. Do not create a
  second cancellation mechanism that bypasses `SessionManager`.
- Keep owner routing and fencing behavior from ADR-0013 and ADR-0015.
- Add focused tests proving cancel rejects missing sessions, rejects non-owner
  requests, returns accepted/cancelling for active turns, is idempotent for idle
  sessions, and emits or exposes final cancelled state.

### PR 3: Workspace inventory, cleanup, and archive manifest

- Extend `WorkspaceProvider` in
  `src/coding_agent/environment/workspace_provider.py` with provider-owned
  workspace inventory and manifest hooks.
- Implement Docker provider inventory/manifest in
  `src/coding_agent/environment/docker_workspace_provider.py` using the existing
  workspace id validation and conservative cleanup paths.
- Add `GET /workspaces`, `GET /workspaces/{workspace_id}`,
  `DELETE /workspaces/{workspace_id}`, and `POST /workspaces/gc`.
- Add session-scoped and workspace-scoped archive manifest endpoints.
- Keep `GET /sessions/{session_id}/workspace` as a compatibility alias.
- Add tests proving workspace list ignores unrelated directories, cleanup skips
  active sessions, cleanup failures are visible, manifest reports file counts and
  bytes, and admin scope is required for global workspace operations.

### PR 4: CLI thin-client commands and safe download UX

- Add HTTP client helpers in `src/coding_agent/remote/client.py` for sessions
  list/status/cancel/close, workspace list/cleanup/delete, archive manifest, and
  archive download.
- Add `remote run` as the recommended one-shot command and keep `remote repl` as
  a compatibility alias.
- Add `remote sessions ...`, `remote workspaces ...`, `remote download`, and
  `remote attach` commands in `src/coding_agent/__main__.py`.
- Before local archive extraction, fetch and display the manifest, then require
  confirmation unless a documented non-interactive flag is provided.
- Add CLI tests in `tests/cli/test_remote_client.py` proving commands call the
  HTTP API, hide tokens, show manifest summaries, and do not overwrite local
  workspaces without confirmation in interactive mode.

## Alternatives Rejected

- Treat P1 as CLI UX work — rejected because Web UI, CI, dashboards, and editor
  clients need the same state and lifecycle operations. The HTTP API is the
  durable product boundary.
- Let the CLI perform workspace cleanup by reconstructing Docker paths — rejected
  because provider ownership, id validation, active-session exclusion, and
  cleanup safety belong on the server.
- Expose admin operations to the normal bearer token until full RBAC exists —
  rejected because P1 can add lightweight admin token scope without designing a
  full identity system.
- Rename existing SSE wire events in place — rejected because existing remote
  prompt and attach clients already consume those event names. Any future event
  versioning needs an explicit migration.
- Implement live sync or patch export in P1 — rejected because manifest-first
  snapshot download improves safety without taking on conflict detection,
  rename handling, concurrent local edits, or incremental sync semantics.
- Add Kubernetes, SSH, VM, microVM, or egress-allowlist providers in P1 —
  rejected because the next gap is operations control around the Docker provider,
  not expanding sandbox runtime choices.

## Acceptance Criteria

- [ ] `test_list_sessions_returns_visible_session_summaries`
- [ ] `test_list_sessions_requires_admin_scope_for_all_sessions`
- [ ] `test_get_session_response_includes_status_and_workspace_summary`
- [ ] `test_cancel_session_turn_returns_cancelling_for_active_turn`
- [ ] `test_cancel_session_turn_exposes_cancelled_final_state`
- [ ] `test_cancel_session_turn_rejects_non_owner`
- [ ] `test_workspaces_list_requires_admin_scope`
- [ ] `test_workspace_cleanup_requires_admin_scope`
- [ ] `test_workspace_cleanup_skips_active_cloud_sessions`
- [ ] `test_workspace_archive_manifest_reports_counts_bytes_and_changes`
- [ ] `test_session_workspace_archive_endpoint_keeps_compatibility_alias`
- [ ] `test_remote_run_uses_create_prompt_manifest_download_flow`
- [ ] `test_remote_download_prompts_before_overwriting_local_workspace`
- [ ] `uv run pytest tests/ui/test_http_server.py tests/ui/test_http_server_workspace_transfer.py tests/cli/test_remote_client.py tests/coding_agent/environment/ -k "session or workspace or remote or cancel or manifest or admin" -v`

## References

- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
- `docs/adr/0017-cloud-workspace-execution.md`
- `docs/adr/0019-remote-client-cloud-workspace-deployment.md`
- `docs/adr/0020-team-production-docker-remote-sandbox-baseline.md`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/ui/auth.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/remote/client.py`
- `src/coding_agent/__main__.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_http_server_workspace_transfer.py`
- `tests/cli/test_remote_client.py`
