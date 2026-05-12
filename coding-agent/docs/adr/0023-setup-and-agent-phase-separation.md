# ADR-0023: Separate setup and agent phases for remote workspaces

**Status**: Proposed
**Date**: 2026-05-12
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0020 made Docker remote workspaces safe enough for controlled team
deployment. ADR-0021 defined the remote operations API. ADR-0022 separated
runtime profiles from sandbox policy so a runtime image is treated as a
toolchain choice, not as the sandbox boundary.

The next real-product gap is dependency and bootstrap setup. Many team repos
need commands such as `uv sync`, `pip install`, `npm install`, `go mod
download`, `cargo fetch`, or project-specific build bootstrap before an agent
can work effectively. Setup often needs limited network access and scoped
secrets such as package registry tokens. The agent phase should not inherit
that network access or those secrets by default.

Without explicit phase separation, deployments are forced into two bad modes:
keep the whole session offline and secret-free, which prevents many real repos
from running, or keep the whole session networked and secret-bearing, which
expands the risk of agent tool execution.

Docker also constrains the design. Docker network policy is container-scoped,
not `docker exec` scoped. A single long-running container cannot safely switch
from "setup has network and secrets" to "agent has no network and no setup
secrets" merely by changing command environment variables. The phase boundary
must therefore be a real container execution boundary.

## Decision

Implement setup/agent phase separation using two Docker containers that share
the same host workspace directory:

- The **setup container** runs explicit setup commands with setup phase policy.
  It may receive setup network access and allowlisted setup secrets.
- The **agent container** runs the coding agent tools with agent phase policy.
  In production it defaults to `network = "none"` and receives no setup secrets.
- The workspace state is shared through the host workspace directory mounted
  into both containers.
- Runtime state is not shared. Process environment, container network
  namespace, injected secrets, running processes, and container lifecycle are
  separate between setup and agent containers.

The setup and agent phases must not rely on `docker exec` against one container
to switch network, secret, or sandbox policy. A logical cloud workspace may own
multiple provider resources, including a setup container, an agent container,
and the shared host workspace directory.

The setup container is ephemeral. After setup succeeds or fails, it should be
removed unless an explicit debug retention option is enabled. The agent
container is created only after setup succeeds. Setup failure fails closed:
the session is marked as setup failed, agent execution does not begin, and the
workspace is cleaned or retained according to the normal session/workspace
lifecycle policy.

### Phase Policy

Phase policy is separate from runtime profile selection:

- A runtime profile selects the allowlisted toolchain image and resource
  defaults.
- Phase policy selects what a phase may do: network mode, injected env/secrets,
  setup commands, timeout, and log redaction.

P0 supports setup commands from server configuration, runtime-profile policy, or
a request field. In production mode, request-provided setup commands must be
enabled by explicit server configuration. The agent cannot dynamically open or
rerun setup from inside the agent phase.

Setup commands must be recorded in session metadata and emitted through the
operations surface so operators can tell what bootstrap work ran. Implementations
may redact command arguments if they include configured secret values.

Suggested configuration shape:

```toml
[remote_phases.setup]
enabled = true
network = "bridge"
timeout_seconds = 600
commands = ["uv sync --all-extras"]
secret_env_allowlist = ["PIP_INDEX_URL", "GITHUB_TOKEN"]
allow_request_commands = false

[remote_phases.agent]
network = "none"
timeout_seconds = 3600
secret_env_allowlist = []
```

The exact field names may change during implementation, but the semantics must
remain: setup policy and agent policy are explicit, separate, and validated.

### Secrets And Redaction

Setup secrets are injected only into the setup container. The agent container
environment is rebuilt from the agent phase policy and must not inherit setup
secret environment variables.

Setup logs and events must redact configured secret values before they are
stored or streamed. Redaction is a best-effort output safety measure, not a
secret containment mechanism.

This ADR does not claim that setup commands cannot leak secrets into workspace
files. If a setup command writes a token into the repository checkout, the agent
container can read that file because both phases share the workspace directory.
P0 does not implement cross-phase file taint tracking, secret scanning, or
automatic cleanup of files created by setup commands.

### Workspace And Cleanup Model

Workspace metadata must be able to track all provider-owned resources for a
logical workspace. For Docker this includes:

- the shared host workspace directory;
- the setup phase container when present;
- the agent phase container when present;
- failed or partially created phase containers.

Cleanup and GC must remove all containers and directories owned by the logical
workspace. They must continue to be conservative: only resources created by this
provider and matching the workspace id/container label policy may be removed.
Cleanup failures must be logged and reflected in workspace status rather than
silently swallowed.

The canonical `CloudWorkspaceBinding.workspace_url` remains the agent execution
endpoint after setup succeeds. The binding or provider metadata may need to
grow to expose phase-owned resources without making clients depend on
Docker-specific container names.

### Session Status And Events

The operations API must expose phase progress. P0 adds or derives at least
these states/events:

- `setup.started`
- `setup.finished`
- `setup.failed`
- `agent.started`

Session status must distinguish setup failure from agent turn failure enough for
clients and operators to understand why the session did not run. An
implementation may add a `phase` field, extend session metadata, or introduce a
compatible status detail as long as existing ADR-0021 clients remain compatible.

## Non-goals

- Do not add dependency caches or artifact caches.
- Do not add a secret manager integration.
- Do not add egress allowlists or registry policy engines.
- Do not add interactive setup approval.
- Do not add per-repo setup recipe discovery.
- Do not add safe sync, patch export, live sync, or conflict detection.
- Do not add persistent attach or a full TUI.
- Do not add Kubernetes, SSH, VM, gVisor, Firecracker, or microVM providers.
- Do not claim that setup commands cannot write secrets into workspace files.
- Do not implement cross-phase file taint tracking or secret scanning.

## Alternatives Rejected

- Use one container and vary `docker exec` environment per phase. This was
  rejected because Docker network mode and several sandbox controls are
  container-scoped. It would create a fake boundary where setup network access
  can persist into the agent container.
- Copy the workspace between fully independent setup and agent workspaces. This
  gives a stronger storage boundary, but it adds copying cost, ownership
  problems, and new synchronization semantics before the product has patch
  export or safe sync.
- Keep all sessions network-free and secret-free. This preserves the current
  production baseline but blocks many real repos that need dependency
  installation.
- Keep network and secrets available for the whole session. This makes dogfood
  easier but violates the security model established by ADR-0020 and ADR-0022.

## Consequences

- Docker workspaces become multi-resource logical workspaces instead of
  one-container workspaces.
- Provisioning becomes a staged lifecycle: create workspace directory, optionally
  run setup container, then create agent container.
- Cleanup and GC must understand all phase containers.
- Session creation can fail before an agent runtime exists when setup fails.
- Remote clients can present better progress and failure messages, but they must
  handle setup failure as a first-class remote session outcome.
- Setup expands real-repo usefulness without weakening the default agent phase
  sandbox.

## Implementation Plan

### PR 1: ADR and schema design

- Add this ADR.
- Decide final request/config field names for setup phase commands and policy.
- Add tests that describe the expected request validation and production
  validation behavior before implementing provider execution.

### PR 2: Workspace provider phase resource model

- Extend `CloudWorkspaceBinding` or provider-private metadata so a logical
  workspace can reference phase-owned resources without exposing arbitrary
  Docker internals to clients.
- Extend `WorkspaceProvider` behavior so provisioning can run setup before the
  agent client factory resolves the workspace.
- Ensure cleanup by binding and cleanup by workspace id remove every
  provider-owned phase container and the shared workspace directory.
- Keep workspace id validation and provider ownership checks from ADR-0020 and
  ADR-0021.

### PR 3: Docker setup container execution

- Start a setup container with setup phase network policy, allowlisted setup
  env/secrets, resource limits, and the shared workspace mount.
- Run only explicit setup commands.
- Enforce setup timeout.
- Redact configured secret values from setup logs/events.
- Remove setup container after success/failure unless explicit debug retention
  is enabled.
- On setup failure, mark setup failed and do not start the agent container.

### PR 4: Docker agent container execution

- Start a distinct agent container after setup succeeds.
- Apply the agent phase sandbox policy, including `network = "none"` in
  production and no setup secrets by default.
- Keep `DockerCloudWorkspaceClient` bound to the agent container.
- Preserve existing file operation, shell execution, archive import/export,
  quota, timeout, and symlink boundary behavior.

### PR 5: HTTP, CLI, docs, and smoke

- Extend `CreateSessionRequest` and remote client helpers with setup command
  inputs only if enabled by server policy.
- Add session metadata/status/events for setup start/success/failure and agent
  start.
- Update `docs/remote-sandbox-production.md` with phase configuration,
  security boundaries, and honest limitations.
- Add a production smoke checklist that demonstrates setup network/secrets
  followed by agent `network = "none"` and no setup secrets.

## Acceptance Criteria

- [ ] Docker provider tests prove setup and agent use distinct containers for
  one logical workspace.
- [ ] Docker provider tests prove the setup container can use setup policy
  network while the agent container uses agent policy `network = "none"`.
- [ ] Docker provider tests prove setup secret env vars are present during setup
  command execution and absent during agent tool/shell execution.
- [ ] Docker provider tests prove setup failure marks the session/workspace as
  setup failed and does not create or start the agent container.
- [ ] Docker provider tests prove cleanup and GC remove all containers labeled
  for the logical workspace, including setup and agent phase containers.
- [ ] HTTP/API tests prove setup commands are accepted only when server policy
  permits them in production.
- [ ] HTTP/API tests prove setup start/success/failure and agent start are
  reflected in session metadata or events.
- [ ] Log/event tests prove configured setup secret values are redacted.
- [ ] Production validation tests reject unsafe setup/agent phase configs where
  required.
- [ ] `uv run pytest tests/coding_agent/environment/ tests/ui/ tests/cli/ -k "setup or phase or workspace or remote" -v`
  passes.
- [ ] `uv run basedpyright --level error` on changed Python files reports no
  errors.
- [ ] `uv run ruff format --check` on changed Python files passes.

## References

- `docs/adr/0020-team-production-docker-remote-sandbox-baseline.md`
- `docs/adr/0021-remote-session-and-workspace-operations-api.md`
- `docs/adr/0022-runtime-profiles-and-sandbox-policy.md`
- `docs/remote-sandbox-production.md`
- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/environment/workspace_provider.py`
- `src/coding_agent/ui/execution_binding.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/remote/client.py`
- `src/coding_agent/__main__.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
- `tests/ui/test_http_server.py`
- `tests/cli/test_remote_client.py`
