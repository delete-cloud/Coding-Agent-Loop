# Remote Sandbox Production Baseline

This guide describes the P0 production baseline for Docker remote workspaces.
It is for controlled team deployments on a trusted Docker host. It is not a
Codex-compatible cloud sandbox platform, a multi-tenant isolation boundary, or a
microVM security claim.

## Configuration

Start the server with an explicit config file:

```bash
export CODING_AGENT_BEARER_TOKEN="$(openssl rand -hex 32)"
coding-agent serve --config /etc/coding-agent/config.toml
```

Example production config:

```toml
[agent]
name = "coding-agent"
model = "gpt-4o"
provider = "openai"

[server]
host = "127.0.0.1"
port = 8080
production = true
bearer_token_env = "CODING_AGENT_BEARER_TOKEN"

[storage]
tape_backend = "pg"
http_session_backend = "pg"
checkpoint_backend = "pg"
dsn = "postgresql://coding_agent:change-me@postgres:5432/coding_agent"
owner_id = "coding-agent-1"
fencing_token = 1
owner_lease_seconds = 30

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

When `server.production = true`, startup fails if bearer auth, Docker workspace
enablement, image allowlist, non-root `exec_user`, quota, GC, resource limits,
or `network = "none"` are missing or unsafe.

Development mode is any config without `server.production = true`. It is useful
for local testing and demos, but the server logs that the configuration is not
safe for team production use.

## Deployment Shapes

### Local Docker Host

Use a host dedicated to remote workspace execution. Create the workspace root
with ownership that allows the server process to create and remove session
directories:

```bash
sudo mkdir -p /var/lib/coding-agent/workspaces
sudo chown coding-agent:coding-agent /var/lib/coding-agent/workspaces
sudo chmod 0700 /var/lib/coding-agent/workspaces
```

The server needs Docker CLI access. Do not mount the Docker socket into agent
workspace containers.

### systemd

Run the service on loopback and place TLS/authenticated ingress in front of it:

```ini
[Service]
User=coding-agent
Group=coding-agent
Environment=CODING_AGENT_BEARER_TOKEN=replace-with-secret
ExecStart=/usr/local/bin/coding-agent serve --config /etc/coding-agent/config.toml
Restart=on-failure
```

### Docker Compose

Compose can run the control-plane service, but remember that the service itself
must be able to talk to the Docker daemon to create workspace containers. Treat
that daemon access as privileged infrastructure access and do not pass it into
workspace containers.

### Reverse Proxy And TLS

Terminate TLS at a reverse proxy or private ingress layer. Keep
`server.host = "127.0.0.1"` when the proxy runs on the same host. Do not expose
the HTTP server without bearer auth in team environments.

## Operations

- `/healthz` reports process liveness.
- `/readyz` checks the session store, rate limiter, and Docker cloud workspace
  provider when cloud workspaces are enabled.
- Quota failures return a clear workspace quota error instead of creating more
  containers.
- Startup cleanup runs when `cleanup_on_startup = true`.
- Periodic GC runs every `gc_interval_seconds` and removes provider-owned
  workspaces older than `max_workspace_age_seconds`.

Relevant logs include production validation failures, workspace creation,
workspace cleanup, startup cleanup, periodic GC, quota exceeded, Docker
operation failure, and workspace archive upload/download failure.

Metrics dashboards, per-user quota, and audit-log identity are P1 or later.

## Remote Operations CLI

Configure a normal user remote and, for operational commands, an admin remote:

```bash
coding-agent remote add team https://coding-agent.example.com --token "$CODING_AGENT_BEARER_TOKEN"
coding-agent remote add team-admin https://coding-agent.example.com --token "$CODING_AGENT_ADMIN_BEARER_TOKEN"
```

Use `remote run` for the recommended one-shot remote workflow:

```bash
coding-agent remote run team --repo . --goal "fix the failing test"
```

`remote run --repo` uploads a local snapshot, streams one prompt, downloads the
final workspace through the archive manifest/archive API, overwrites the local
checkout while preserving `.git`, and then closes the remote session. Use
`--yes` only when the local checkout is already recoverable and you want to skip
the manifest confirmation prompt.

`remote repl` is a compatibility alias for this one-shot workflow. It is not a
persistent REPL/TUI. `attach` sends one prompt to an existing session; the HTTP
events stream is the lower-level monitor primitive for future richer clients.

Useful day-to-day operations:

```bash
coding-agent remote sessions list team
coding-agent remote sessions status team <session-id>
coding-agent remote sessions cancel team <session-id>
coding-agent remote sessions close team <session-id>

coding-agent remote download team --session <session-id> --repo .

coding-agent remote workspaces list team-admin
coding-agent remote workspaces cleanup team-admin --stale
coding-agent remote workspaces rm team-admin <workspace-id>
```

Normal tokens are intended for creating, prompting, cancelling, closing, and
downloading work owned by that token. Admin tokens are required for global
workspace inspection and cleanup.

For non-interactive automation, make approval behavior explicit. The current
remote prompt stream can request approval for tools such as `bash_run`; provide
stdin approval input or use a deployment policy that matches the workflow.

## Docker Security Boundary

The P0 Docker provider uses these production requirements:

- `network = "none"`
- dropped Linux capabilities
- `no-new-privileges`
- explicit CPU, memory, and PID limits
- explicit image allowlist
- explicit non-root `exec_user`
- host workspace path validation

This is Docker isolation. It is not microVM isolation. P0 does not claim safety
if the Docker daemon, container runtime, host kernel, or configured runtime image
is compromised.

P0 does not implement read-only root filesystems, seccomp profiles, user
namespaces, egress allowlists, Kubernetes scheduling, SSH providers, VM
providers, or microVM providers.

## Snapshot Transfer Semantics

Remote runs use snapshot round-trip transfer:

1. The local repo is packed as a bounded `tar.gz` archive encoded as base64.
2. The server extracts that archive into a Docker workspace.
3. The agent executes in the remote workspace.
4. The final remote workspace is downloaded.
5. The local checkout is overwritten while local `.git` is preserved.

This is not live sync. P0 does not support incremental patch export, concurrent
local edit merging, efficient large-repo delta sync, or automatic conflict
resolution.

Before using `remote run --repo` or `remote repl --repo`, keep your local work
recoverable with a commit, stash, or backup.

## Production Smoke

A minimal production smoke should exercise the real HTTP boundary and Docker
provider:

1. Start `coding-agent serve --config /etc/coding-agent/config.toml` with
   `server.production = true`, bearer token environment variables, Docker image
   allowlist, non-root `exec_user`, quota, GC, and resource limits configured.
2. Check readiness:

   ```bash
   curl -fsS -H "Authorization: Bearer $CODING_AGENT_BEARER_TOKEN" \
     http://127.0.0.1:8080/readyz
   ```

3. Run a small repository through `remote run --repo`.
4. Confirm the downloaded workspace contains the remote change.
5. Confirm successful one-shot runs leave no active sessions or workspaces:

   ```bash
   coding-agent remote sessions list team
   coding-agent remote workspaces list team-admin
   ```

This verifies production config loading, auth, Docker provider readiness,
workspace execution, approval forwarding, archive manifest/download, local
restore, and session/workspace cleanup.
