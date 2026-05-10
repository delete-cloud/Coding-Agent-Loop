# ADR-0020: Adopt a team production baseline for Docker remote workspaces

**Status**: Accepted
**Date**: 2026-05-10
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0017 established the provider-neutral `CloudEnvironment` and
`CloudWorkspaceClient` boundary. ADR-0018 kept cloud-aware child agents
owner-local. ADR-0019 then made remote Docker workspaces executable: a local
client can upload a snapshot, create a remote session, execute tools in a
Docker-backed cloud workspace, and download a final workspace snapshot.

That implementation is enough for an MVP, but it is not yet a team production
baseline. A team deploying `coding-agent serve` on an internal Docker host needs
clear deployment configuration, fail-fast safety checks, bounded workspace
growth, predictable cleanup after client crashes or server restarts, and honest
documentation of the snapshot transfer model. Without those controls a server
can appear to work while running without authentication, creating unbounded
containers, keeping stale workspace directories forever, or implying stronger
sandbox and synchronization guarantees than Docker snapshot execution actually
provides.

The goal of this ADR is not to build a full cloud sandbox platform or match a
microVM-level Codex sandbox. The goal is to make the existing Docker remote
workspace path safe enough for controlled team deployment.

## Decision

Adopt an explicit production mode for `coding-agent serve`:

```toml
[server]
production = true
```

Production mode is never inferred from host, port, TLS, reverse proxy, or any
other ambient condition. Development and demo runs remain convenient when
`production` is absent or false, but startup must log that the configuration is
not safe for team production use. When `production = true`, startup must fail
fast if any required authentication, Docker workspace, resource, quota, or GC
setting is missing or unsafe.

Production deployments should use a configuration file passed explicitly to the
server:

```bash
coding-agent serve --config /etc/coding-agent/config.toml
```

The P0 production configuration contract is:

```toml
[server]
host = "127.0.0.1"
port = 8080
production = true
bearer_token_env = "CODING_AGENT_BEARER_TOKEN"

[cloud_workspace]
enabled = true
provider = "docker"
workspace_root = "/var/lib/coding-agent/workspaces"

image = "coding-agent-runtime:2026-05-10"
image_allowlist = ["coding-agent-runtime:2026-05-10"]
exec_user = "1000:1000"

max_active_workspaces = 8
max_workspace_age_seconds = 86400
gc_interval_seconds = 300
cleanup_on_startup = true

network = "none"
cpus = "2"
memory = "4g"
pids_limit = 512
```

When `production = true`, all of the following are required:

- A bearer token is configured. `server.bearer_token_env` is preferred and the
  referenced environment variable must exist and be non-empty. A direct
  `server.bearer_token` remains allowed for compatibility but is discouraged in
  documentation.
- `[cloud_workspace] enabled = true` and `provider = "docker"`.
- `cloud_workspace.image_allowlist` is explicitly configured and non-empty.
- `cloud_workspace.exec_user` is explicitly configured and must not be root.
  Root-equivalent values such as `root`, `0`, `0:0`, and `0:<gid>` are rejected.
- `cloud_workspace.max_active_workspaces > 0`.
- `cloud_workspace.max_workspace_age_seconds > 0`.
- `cloud_workspace.gc_interval_seconds > 0`.
- Per-workspace resource limits are explicit: `cpus`, `memory`, and
  `pids_limit > 0`.
- `cloud_workspace.network = "none"`. P0 does not add an egress allowlist mode.

Docker workspace lifecycle must be bounded:

- Closing a provisioned session cleans up its workspace directory and container.
- Startup cleanup can remove stale workspaces when `cleanup_on_startup = true`.
- Periodic GC removes stale workspaces older than
  `max_workspace_age_seconds` every `gc_interval_seconds`.
- Quota enforcement rejects new workspace creation when
  `max_active_workspaces` is reached.
- GC and quota must only consider provider-owned workspace ids that match the
  Docker provider workspace id policy. They must not delete arbitrary
  directories or containers under the workspace root.
- Cleanup failures must be logged with enough context to diagnose the failed
  workspace id or Docker operation.

Observability remains simple in P0:

- `/healthz` continues to report process liveness.
- `/readyz` continues to include provider readiness when cloud workspaces are
  enabled.
- Logs must cover production validation failures, workspace creation, workspace
  cleanup, startup cleanup, periodic GC, quota exceeded, Docker operation
  failures, and archive upload/download failures.
- Metrics are a P1 concern and do not block this ADR.

The remote UX must be honest about the current interaction model:

- `remote repl` is documented as a one-shot remote run, not a durable local TUI
  replacement.
- `attach` is documented as sending one prompt to an existing remote session,
  not a full interactive attach loop.
- Output and documentation should expose the remote name, session id, cleanup
  status, and workspace download/overwrite behavior where relevant.

The synchronization model remains explicit snapshot round-trip:

1. The local repo is uploaded as a bounded `tar.gz` archive encoded as base64.
2. The agent executes in the remote Docker workspace.
3. The final remote workspace is downloaded as a bounded `tar.gz` archive.
4. Local checkout files are overwritten while local `.git` is preserved.

Documentation must state that P0 does not support live sync, incremental patch
export, concurrent local edit merging, efficient large-repo delta sync, or
automatic conflict resolution. Users should commit, stash, or otherwise make
their local work recoverable before running a remote snapshot workflow.

## Consequences

- Production deployments gain a mechanical safety gate. Servers that omit
  authentication, quota, GC, non-root execution, resource limits, or Docker
  network isolation fail during startup instead of serving unsafe requests.
- Local development remains convenient because production validation is gated by
  explicit `server.production = true`.
- Configuration becomes part of the public deployment contract. Future changes
  to production field names must either preserve compatibility or be captured in
  a follow-up ADR.
- Docker provider code becomes responsible for provider-owned workspace
  accounting and stale cleanup. This adds operational state management, but it
  prevents unbounded resource growth after client crashes or server restarts.
- P0 still relies on Docker daemon isolation. This ADR reduces common deployment
  footguns but does not protect the host from a Docker daemon compromise.

## Implementation Plan

### PR 1: Explicit server config and production validation

- Modify `src/coding_agent/__main__.py` so `serve` accepts
  `--config /path/to/config.toml`.
- Modify `src/coding_agent/ui/http_server.py` so server, storage, and cloud
  workspace config are loaded from the explicit config path when present instead
  of depending only on the package-local `agent.toml`.
- Add production validation at HTTP server startup and in the session manager
  construction path so unsafe production config fails before serving requests.
- Follow the existing `agentkit.config.loader.load_config(Path)` pattern for
  TOML parsing. Do not introduce a second TOML parser or a profile system.
- Use one environment variable, `CODING_AGENT_SERVER_CONFIG`, as the bridge
  between the Click `serve --config` command and module-level HTTP server
  construction.
- Do not infer production mode from `host`, `port`, TLS, or reverse proxy state.
- Add tests in `tests/ui/test_http_server.py` for explicit config loading,
  development-mode warning, accepted production config, and each fail-fast
  production validation requirement.

### PR 2: Docker workspace quota

- Extend `src/coding_agent/environment/docker_workspace_provider.py` to parse
  `max_active_workspaces`.
- Before provisioning a new Docker workspace, count active provider-owned
  workspace directories under `workspace_root` and reject creation with a clear
  `ValueError` when quota is reached.
- Count only valid workspace ids accepted by the Docker provider workspace id
  policy.
- Preserve the existing Docker provider pattern: validate config in
  `_docker_workspace_provider_config`, derive host paths through
  `_workspace_root_for_id`, and avoid trusting client-provided workspace ids.
- Add tests in
  `tests/coding_agent/environment/test_docker_workspace_provider.py` proving
  quota rejection happens before `docker run` and ignores unrelated directories.

### PR 3: Docker workspace GC

- Extend `src/coding_agent/environment/workspace_provider.py` with a provider
  GC hook.
- Implement Docker provider GC for stale provider-owned workspace ids older than
  `max_workspace_age_seconds`.
- Remove matching Docker containers and workspace directories using the same
  conservative cleanup path as session close.
- Wire startup cleanup and periodic cleanup from `src/coding_agent/ui/http_server.py`
  based on `cleanup_on_startup`, `gc_interval_seconds`, and
  `max_workspace_age_seconds`.
- Keep cleanup best-effort at the periodic task boundary: log failures with
  workspace context, continue the loop, and do not silently suppress production
  validation errors.
- Do not remove non-matching directories, non-`ws-*` names, or containers that
  do not correspond to provider-owned workspace ids.
- Add tests proving startup cleanup is called when configured, periodic cleanup
  runs at the configured interval, stale workspaces are removed, fresh
  workspaces are preserved, and unrelated directories are ignored.

### PR 4: Production deployment documentation and UX wording

- Add `docs/remote-sandbox-production.md` with:
  - Example production config.
  - Docker host, systemd, Docker Compose, and reverse-proxy/TLS deployment notes.
  - Required environment variables and bearer token handling.
  - Workspace root placement and ownership guidance.
  - Docker image allowlist and runtime image guidance.
  - Resource limits, quota, GC, and failure behavior.
  - Health/readiness checks and operational troubleshooting.
  - Docker isolation boundaries and non-goals.
  - Snapshot round-trip semantics and local overwrite risk.
- Update CLI help/output in `src/coding_agent/__main__.py` so `remote repl` and
  `attach` do not imply a persistent REPL/TUI.
- Add focused CLI tests in `tests/cli/test_remote_client.py` for the wording.

## Alternatives Rejected

- Infer production mode from host, port, TLS, or reverse proxy state — rejected
  because inference can both miss unsafe deployments and break legitimate local
  production topologies behind a proxy.
- Make strict production settings the default — rejected because local
  development, tests, and demos should not require production token, quota, and
  Docker user configuration.
- Keep only documentation warnings without fail-fast validation — rejected
  because teams often deploy once a server appears to work; unsafe production
  deployment must be mechanically blocked.
- Add live sync or patch export in P0 — rejected because conflict detection,
  rename handling, ignore rules, and concurrent local edits are a separate
  protocol.
- Add Kubernetes, SSH, VM, or microVM providers in P0 — rejected because the
  current gap is making the existing Docker provider deployable, not expanding
  provider scope.
- Claim Codex or microVM-grade sandboxing — rejected because P0 still depends on
  Docker daemon isolation and does not protect the host if the Docker daemon or
  container runtime boundary is compromised.

## Acceptance Criteria

- [ ] `tests/ui/test_http_server.py::test_http_server_loads_config_from_explicit_server_config`
- [ ] `tests/ui/test_http_server.py::test_production_config_accepts_safe_docker_workspace_config`
- [ ] `tests/ui/test_http_server.py::test_production_config_rejects_unsafe_remote_workspace_config`
- [ ] `tests/ui/test_http_server.py::test_lifespan_runs_startup_cloud_workspace_cleanup_when_configured`
- [ ] `tests/ui/test_http_server.py::test_periodic_cloud_workspace_gc_runs_at_configured_interval`
- [ ] `tests/coding_agent/environment/test_docker_workspace_provider.py::test_docker_workspace_provider_rejects_provision_when_active_workspace_quota_is_reached`
- [ ] `tests/coding_agent/environment/test_docker_workspace_provider.py::test_docker_workspace_provider_quota_ignores_unowned_workspace_directories`
- [ ] `tests/coding_agent/environment/test_docker_workspace_provider.py::test_docker_workspace_provider_gc_removes_only_stale_owned_workspaces`
- [ ] `tests/cli/test_remote_client.py::test_remote_repl_help_describes_one_shot_remote_run`
- [ ] `tests/cli/test_remote_client.py::test_attach_help_describes_single_prompt_attach`
- [ ] `uv run pytest tests/ui/test_http_server.py -k "production or explicit_server_config or cloud_workspace_gc" -v`
- [ ] `uv run pytest tests/coding_agent/environment/test_docker_workspace_provider.py -k "quota or gc or provision" -v`
- [ ] `uv run pytest tests/cli/test_remote_client.py -k "remote_repl_help or attach_help" -v`
- [ ] `uv run basedpyright src/coding_agent/ui/http_server.py src/coding_agent/environment/workspace_provider.py src/coding_agent/environment/docker_workspace_provider.py tests/ui/test_http_server.py tests/coding_agent/environment/test_docker_workspace_provider.py tests/cli/test_remote_client.py`
- [ ] `uv run ruff format src/coding_agent tests/ui/test_http_server.py tests/coding_agent/environment/test_docker_workspace_provider.py tests/cli/test_remote_client.py --check`
- [ ] `git diff --check`

## References

- `docs/adr/0017-cloud-workspace-execution.md`
- `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
- `docs/adr/0019-remote-client-cloud-workspace-deployment.md`
- `src/coding_agent/__main__.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/remote/client.py`
- `tests/ui/test_http_server.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
- `tests/cli/test_remote_client.py`
