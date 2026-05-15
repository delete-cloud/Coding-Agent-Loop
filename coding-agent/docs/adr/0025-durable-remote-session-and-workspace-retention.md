# ADR-0025: Persist remote session and workspace retention state

**Status**: Accepted / implemented through PRs #161-#171
**Date**: 2026-05-14
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0012 added PostgreSQL-backed HTTP session persistence, and ADR-0013
defined the later owner/lease/fencing model for multi-instance HTTP sessions.
ADR-0020 through ADR-0024 then made Docker remote workspaces production-usable:
sessions can provision Docker workspaces, run on a controlled host, expose
operations APIs, publish Git-backed results, and clean up stale local
directories and containers.

That baseline still treats remote workspace lifecycle as mostly provider-local
state. Session metadata may be in PostgreSQL, but the durable relationship
between a session, its workspace id, workspace host, provider config identity,
container ids, retention policy, result publication state, and cleanup failures
is not first-class queryable state. After a server restart or operator handoff,
the system can rediscover directories and containers, but it cannot reliably
answer which workspace should be retained, which session owns it, whether it is
pinned, whether cleanup already failed, or which host/provider must be contacted
to operate on it.

The design must not assume PostgreSQL and the workspace filesystem live on the
same machine. PostgreSQL is a control-plane store for metadata and recoverable
references. Workspace data remains on the selected workspace host, such as a
Docker host with `cloud_workspace.workspace_root`. A server instance may be able
to read PostgreSQL while not having local filesystem access to a particular
workspace root. All workspace operations therefore need an explicit provider and
host identity before using any host path.

This mirrors the useful part of YA Claw's approach in
`Wh1isper/ya-mono/packages/ya-claw`: relational storage owns queryable
session/run/runtime state, while the filesystem owns workspace files and
run-store blobs. Docker sandbox/container metadata is stored as session/runtime
metadata and drives retention and TTL cleanup. This repository should adopt the
same control-plane/data-plane split without copying YA Claw's single-workspace
product shape.

## Decision

Add durable remote session and workspace retention state as a PostgreSQL-backed
control-plane layer. PostgreSQL stores queryable metadata and references.
Workspace files, Git checkouts, archives, run blobs, and Docker container
contents remain on the workspace host or provider-specific storage.

The new model has four durable concepts:

1. **Remote session record** — existing HTTP session metadata continues to be
   stored through the configured session store. When PostgreSQL is enabled, the
   session record must preserve enough remote binding metadata to resume
   inspection after restart: `workspace_id`, `workspace_record_id`,
   workspace source kind, result refs, publication refs, owner label, and phase
   status.
2. **Workspace record** — a new provider-agnostic schema tracks the logical
   workspace lifecycle independently from session status. Each record still
   names its concrete provider, such as `docker`.
3. **Workspace resource record** — provider-owned resources such as Docker
   agent/setup containers are tracked as child records or structured resource
   metadata so cleanup/GC can operate deterministically.
4. **Retention policy** — each workspace has an explicit durable policy that
   controls close and GC behavior.

### Control plane versus data plane

PostgreSQL must never be treated as a filesystem proxy. A workspace record may
store paths, URLs, ids, hashes, and provider references, but those fields are
opaque until resolved by the matching provider on a compatible workspace host.

Required workspace identity fields:

- `workspace_record_id`, a durable database primary key for the workspace
  record; session records reference this value to resume the exact workspace
  record after restart
- `workspace_id`
- `provider`, initially `docker`
- `provider_instance_id`, a stable deployment-local id for the workspace host or
  provider instance
- `workspace_root_ref`, the configured root/path reference meaningful only to
  that provider instance
- `workspace_host_label`, a human-readable host/deployment label for operations
- `session_id`
- `owner_label`
- `source_kind`, such as `git` or `snapshot`
- `source_ref`, redacted and bounded metadata for the source repository or
  snapshot
- `created_at`, `updated_at`, `last_used_at`, and optional `expires_at`

`provider_instance_id` is required because a PostgreSQL database may be shared
by multiple coding-agent server processes or may run on a different server from
the Docker host. A process may only perform provider-local filesystem or Docker
operations for workspace records whose `provider_instance_id` matches its
configured provider instance. Non-matching records can still be listed as
remote/unavailable state, but local cleanup must fail closed with a clear
message instead of attempting to interpret paths on the wrong host.

### Workspace status and retention policy

Workspace status is separate from session status. Use these initial statuses:

- `provisioning`
- `active`
- `idle`
- `retained`
- `stale`
- `cleaning`
- `cleaned`
- `cleanup_failed`
- `lost`

`lost` means a durable workspace record exists but the provider resources cannot
be found or verified. Examples include missing Docker containers, missing
workspace directories, inconsistent resource ids, provider API 404s, repeated
health-check timeouts, or credentials that no longer permit verification. The
server should mark the workspace `lost`, keep the durable record for operator
inspection, and require explicit recovery or cleanup rather than silently
recreating or deleting unrelated resources.

Use these initial retention policies:

- `delete_on_close` — closing the session cleans up the workspace and marks it
  `cleaned` when successful.
- `ttl` — workspace is retained after close or idle transition until
  `expires_at`.
- `pinned` — workspace is retained until an explicit unpin or delete operation.
- `manual` — workspace is retained for operator-managed cleanup and excluded
  from automatic GC.

Production defaults stay conservative: newly provisioned remote sessions use
`delete_on_close` unless configuration chooses a bounded `ttl`. Operators can
pin or switch retention policy through admin operations. Normal users may retain
only their own sessions/workspaces if the server exposes that capability; admin
tokens can manage all records.

### Close, shutdown, and GC semantics

Closing a session must no longer mean "always delete the workspace." It means:

1. close/cancel the runtime task;
2. transition the session to closed or terminal state;
3. evaluate the workspace retention policy;
4. either clean provider resources or mark the workspace retained/idle with a
   durable reason.

Server shutdown must stop local runtime state without deleting retained
workspace records. This aligns with the existing
`shutdown_session_runtime()` distinction: releasing runtime resources and
deleting persisted session/workspace metadata are different operations.

GC must be driven by durable workspace records plus provider verification:

- select workspace records eligible for cleanup by status, policy, and
  `expires_at`;
- require matching `provider_instance_id` before provider-local cleanup;
- clean only resources known to belong to this system through workspace records,
  Docker labels, and workspace id policy;
- update status to `cleaned` or `cleanup_failed` with the error summary;
- preserve records long enough for audit/debugging even after deleting files and
  containers.

Filesystem scanning remains a reconciliation fallback, not the source of truth.
If the provider sees a directory/container without a workspace record, it should
surface it as an orphan candidate for admin cleanup, not silently attach it to a
session.

### API and CLI surface

Extend the remote operations API from ADR-0021 with retention-aware operations:

- `GET /sessions/{session_id}` includes workspace retention summary when a
  workspace is bound.
- `GET /workspaces` lists durable workspace records, including remote/unavailable
  provider-instance state.
- `GET /workspaces/{workspace_id}` returns durable metadata plus provider
  reconciliation status when available.
- `POST /workspaces/{workspace_id}/retain` changes retention policy or expiry.
- `POST /workspaces/{workspace_id}/pin` is a convenience for
  `retention_policy = "pinned"`.
- `POST /workspaces/{workspace_id}/unpin` applies an explicit requested bounded
  policy when provided; otherwise it falls back to the configured
  `remote_retention.default_policy`. The implementation does not need a
  `previous_retention_policy` field in P0.
- `DELETE /workspaces/{workspace_id}` performs explicit cleanup and marks the
  durable record with the result.

CLI commands should remain thin clients:

```bash
coding-agent remote sessions status <remote> <session-id>
coding-agent remote workspaces status <remote> <workspace-id>
coding-agent remote workspaces retain <remote> <workspace-id> --ttl 7d
coding-agent remote workspaces pin <remote> <workspace-id>
coding-agent remote workspaces unpin <remote> <workspace-id> --ttl 24h
coding-agent remote workspaces rm <remote> <workspace-id>
```

The CLI must show whether the current server can operate on the workspace host.
For example, a workspace record from `provider_instance_id = "docker-a"` viewed
from a server configured as `provider_instance_id = "docker-b"` should be
reported as durable metadata only, not as locally cleanable.

### Configuration

Production retention requires PostgreSQL-backed HTTP sessions and durable
workspace records:

```toml
[storage]
http_session_backend = "pg"
tape_backend = "pg"
checkpoint_backend = "pg"
dsn = "postgresql://coding_agent:change-me@postgres:5432/coding_agent"

[cloud_workspace]
enabled = true
provider = "docker"
provider_instance_id = "docker-host-a"
workspace_host_label = "docker-host-a.internal"
workspace_root = "/var/lib/coding-agent/workspaces"

[remote_retention]
enabled = true
default_policy = "delete_on_close"
default_ttl_seconds = 86400
allow_user_pin = false
```

`[remote_retention]` is the canonical section name for this ADR. When
`cloud_workspace.enabled = true` and `remote_retention.enabled = false`, the
server keeps the legacy provider-local behavior from ADR-0020: session close,
startup cleanup, periodic GC, quota, and workspace listing rely on the Docker
provider's filesystem/container discovery rather than durable workspace records.
That fallback remains acceptable for development and compatibility, but it does
not provide restart-safe retention or cross-host workspace ownership.

`provider_instance_id` must be stable across restarts of the same workspace
host. If it changes, existing workspace records become non-local to that server
until an operator migrates or reconciles them. This is intentional: guessing
that a path on one host means the same thing on another host is unsafe.

Development mode may continue using in-memory session metadata and filesystem
workspace scanning. Production mode must fail fast if durable retention is
enabled without PostgreSQL session storage or without a configured
`provider_instance_id`. When `remote_retention.enabled = true`,
`remote_retention.default_policy` must be one of `delete_on_close`, `ttl`,
`pinned`, or `manual`; `default_ttl_seconds` must be positive when the default
policy is `ttl`; and `allow_user_pin` must be explicit so deployments choose
whether non-admin tokens may create unbounded retention.

### Relationship to result publication

ADR-0024 made remote results review-first and Git-backed when possible. Durable
retention complements that model:

- session result refs and publication metadata should be stored in session or
  workspace metadata;
- published branch/PR state should remain inspectable after runtime shutdown;
- patch/archive exports remain generated from the workspace provider while the
  workspace exists;
- once a workspace is cleaned, APIs should return durable result/publication
  metadata and clearly report that provider-local exports are no longer
  available.

## Alternatives Rejected

- Store workspace files or archives directly in PostgreSQL — rejected because
  PostgreSQL is the control-plane store, not the workspace data plane. Large
  repositories, Git checkouts, build outputs, and archives belong in provider
  storage or object storage if added later.
- Keep using only filesystem scans and Docker labels — rejected because they do
  not preserve ownership, retention policy, cleanup failures, result refs, or
  provider host identity across restart and operator handoff.
- Assume PostgreSQL and `workspace_root` are on the same server — rejected
  because production deployments may run PostgreSQL separately from the Docker
  workspace host. Host paths are provider-local references, not globally valid
  filesystem paths.
- Make `close_session` always keep the workspace — rejected because production
  defaults must still avoid unbounded resource growth. Retention must be
  explicit and policy-driven.
- Add full multi-tenant identity, per-user quota, or a dashboard in this ADR —
  rejected because ADR-0021 already chose lightweight token ownership for P1.
  This ADR only adds durable lifecycle records and retention operations.
- Implement provider migration between Docker hosts — rejected because moving a
  live workspace between hosts requires copy/sync semantics outside this
  decision. This ADR only makes the host identity explicit.

## Acceptance Criteria

- [x] `test_pg_workspace_store_round_trips_remote_workspace_record`
- [x] `test_workspace_record_requires_provider_instance_id_in_production`
- [x] `test_close_session_retains_workspace_when_policy_is_pinned`
- [x] `test_close_session_deletes_workspace_when_policy_is_delete_on_close`
- [x] `test_workspace_gc_skips_non_local_provider_instance`
- [x] `test_workspace_gc_marks_cleanup_failed_with_error_summary`
- [x] `test_workspace_list_reports_durable_remote_unavailable_state`
- [x] `test_remote_workspace_pin_and_unpin_update_retention_policy`
- [x] `test_cleaned_workspace_keeps_result_publication_metadata`
- [x] `uv run pytest tests/ui tests/coding_agent/environment tests/cli/test_remote_client.py -k "workspace_record or retention or provider_instance or pin or cleanup_failed or result_refs or workspaces" -q`
- [x] `uv run basedpyright --level error src/coding_agent/ui src/coding_agent/environment`

## Implementation Plan

Implemented in PRs #161 through #171. PRs #161-#170 landed the ADR, HTTP
contracts, PostgreSQL workspace metadata store, production validation, retention
policy behavior on close, durable workspace list/status, durable GC, admin
retention operations, and result/publication refs. PR #171 added the CLI
thin-client commands for workspace status, retain, pin, and unpin.

### PR 1: ADR and schema contracts

- Add this ADR.
- Add Pydantic schema models in `src/coding_agent/ui/schemas.py` for durable
  workspace record views, retention policy requests, and cleanup results.
- Add HTTP route contract tests for retention endpoints with unsupported
  operations returning explicit errors until the store exists.
- Do not change default cleanup behavior in this PR.

### PR 2: PostgreSQL workspace metadata store

- Add a PostgreSQL-backed workspace metadata store beside the existing session
  store patterns in `src/coding_agent/ui/session_store.py` or a narrowly scoped
  new module such as `src/coding_agent/ui/workspace_store.py`.
- Store workspace records and provider resource records with explicit
  `provider_instance_id`.
- Reuse the configured `[storage].dsn`; do not introduce a second database
  connection string for workspace metadata in the first implementation.
- Add tests for create/update/list/get/status transitions and cleanup failure
  persistence.

### PR 3: Provider instance identity and production validation

- Add `cloud_workspace.provider_instance_id` and
  `cloud_workspace.workspace_host_label` config parsing.
- In production mode, require `provider_instance_id` whenever durable retention
  is enabled.
- Ensure provider-local operations fail closed when the workspace record belongs
  to another provider instance.
- Update `docs/remote-sandbox-production.md` with an explicit note that
  PostgreSQL and workspace hosts may be separate machines.

### PR 4: Session close and retention policy

- Change `SessionManager.close_session()` and HTTP `DELETE /sessions/{id}` so
  close evaluates workspace retention policy before cleanup.
- Keep `shutdown_session_runtime()` as runtime-only release and do not delete
  retained workspace records from that path.
- Add admin/user retention operations and CLI thin-client commands.
- Preserve existing delete-on-close behavior as the production default.

### PR 5: Durable GC and reconciliation

- Drive periodic GC from workspace records instead of only scanning
  `workspace_root`.
- Keep filesystem scan as an orphan detection/reconciliation path.
- Record cleanup attempts, success, and failure summaries in durable metadata.
- Add admin visibility for orphan directories/containers that match provider id
  policy but have no workspace record.

### PR 6: Result refs and cleaned-workspace behavior

- Persist ADR-0024 result/publication refs on the session/workspace record.
- Ensure APIs can still show final result/publication metadata after provider
  files are cleaned.
- Return clear unavailable errors for diff/patch/archive after workspace cleanup
  instead of pretending the workspace still exists.

## References

- `docs/adr/0012-complete-phase1-postgresql-http-session-persistence.md`
- `docs/adr/0013-define-phase2-multi-instance-session-ownership-for-pg-http-sessions.md`
- `docs/adr/0020-team-production-docker-remote-sandbox-baseline.md`
- `docs/adr/0021-remote-session-and-workspace-operations-api.md`
- `docs/adr/0024-remote-result-publication.md`
- `docs/remote-sandbox-production.md`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/session_store.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/environment/workspace_provider.py`
- `tests/ui/test_http_server.py`
- `tests/cli/test_remote_client.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
- `https://github.com/Wh1isper/ya-mono/tree/main/packages/ya-claw`
