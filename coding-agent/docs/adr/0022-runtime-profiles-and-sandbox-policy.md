# ADR-0022: Separate runtime profiles from sandbox policy for remote workspaces

**Status**: Proposed
**Date**: 2026-05-11
**Decision owner**: repository maintainer / current product owner

## Context

ADR-0017 introduced provider-neutral cloud workspace execution. ADR-0020 made
the Docker remote workspace path safe enough for controlled team deployment, and
ADR-0021 defined the HTTP operations surface around sessions and workspaces.

The current Docker provider still has an important modeling problem:
`cloud_workspace.image` is doing too much work in people's heads. A Docker image
describes what tools and dependencies are available in the workspace, but it is
not the sandbox policy. Treating a runtime image as the security boundary makes
it too easy to confuse "this image has Python" with "this workspace is safe",
or to add a future `--image` flag that lets users or agents choose arbitrary
images at runtime.

The remote workspace model should instead match the product boundary:

- The environment backend decides where execution happens: Docker today,
  Kubernetes, gVisor, Firecracker, or another backend later.
- The runtime profile decides which allowlisted toolchain image and resource
  defaults a project receives.
- The sandbox policy decides the network, user, capabilities, filesystem,
  secrets, and resource constraints applied to that runtime.

This keeps Docker as a practical remote execution backend v0 without pretending
Docker images are the entire sandbox story.

## Decision

Keep Docker as the first remote workspace backend, but introduce
allowlisted runtime profiles as the user-facing selector for workspace
toolchains.

Production deployments may define profiles under:

```toml
[runtime_profiles.python-basic]
provider = "docker"
image = "python:3.11-slim@sha256:..."
network = "none"
cpus = "1"
memory = "1g"
pids_limit = 256
exec_user = "1000:1000"
tools = ["python", "pip"]

[runtime_profiles.universal]
provider = "docker"
image = "ghcr.io/delete-cloud/coding-agent-universal:2026-05-11"
network = "none"
cpus = "2"
memory = "4g"
pids_limit = 512
exec_user = "1000:1000"
tools = ["python", "node", "go", "git", "rg", "curl"]
```

`runtime_profiles.<name>.image` is the runtime image. It is only a toolchain and
dependency declaration. It is not a security claim.

The sandbox policy remains explicit configuration that maps to provider-specific
controls. For Docker P1 this means:

- `network = "none"` unless a later ADR adds egress allowlists.
- non-root `exec_user`.
- dropped Linux capabilities.
- `no-new-privileges`.
- CPU, memory, and PID limits.
- host workspace path validation.
- no Docker socket exposure.

P1 does not add an open-ended CLI `--image` flag. Remote clients may request a
profile by name:

```bash
coding-agent remote run team --repo . --runtime universal --goal "..."
```

The request payload includes `workspace_source.runtime_profile = "universal"`.
The server resolves that name against its configured `runtime_profiles` map.
Unknown profiles fail fast before container creation. The agent and client never
send arbitrary image names.

Compatibility is preserved:

- Existing `[cloud_workspace].image`, `image_allowlist`, `network`, `cpus`,
  `memory`, `pids_limit`, and `exec_user` continue to work as the default Docker
  runtime when no runtime profile is requested.
- Production validation still requires explicit image allowlist and sandbox
  controls for the default Docker workspace path.
- Runtime profiles are an additive selection layer. They do not remove the
  existing Docker provider config in this ADR.

The recommended dogfood path should use a `universal` runtime profile rather
than `python:3.11-slim` once a universal image exists. `python:3.11-slim`
remains appropriate for minimal smoke tests and pure file operations.

## Non-goals

- Do not replace Docker with Kubernetes, gVisor, Firecracker, or microVMs in
  this ADR.
- Do not claim Codex CLI or microVM-grade sandboxing.
- Do not add arbitrary per-request Docker image selection.
- Do not add live sync, patch export, CRDTs, or concurrent local edit merging.
- Do not implement setup phase versus agent phase separation yet.
- Do not add egress allowlists, secret phase scoping, or runtime image build
  automation yet.

## Alternatives Rejected

- Let clients pass `--image` directly. This was rejected because it moves
  supply-chain trust, cache behavior, and sandbox policy decisions from the
  server operator to the caller. It also makes production runs harder to
  reproduce.
- Keep using only `[cloud_workspace].image`. This was rejected because it keeps
  overloading one field with toolchain selection, deployment defaults, and
  perceived security meaning.
- Replace Docker before adding profiles. This was rejected because the current
  product gap is not the Docker backend itself; it is the missing separation
  between environment backend, runtime toolchain, and sandbox policy.

## Consequences

- The public remote UX moves from "choose an image" to "choose an allowlisted
  runtime profile".
- Docker provider config becomes slightly richer because it must resolve an
  optional profile before provisioning.
- Future backends can implement the same profile name contract without exposing
  Docker-specific image flags to clients.
- Runtime image supply-chain control stays server-side. Deployment owners can
  pin image digests and review profile definitions.
- Sandbox hardening remains independently visible. A profile with a larger
  toolchain does not imply looser network, user, capability, or resource policy.

## Implementation Plan

### PR 1: ADR and minimal runtime profile selection

- Add this ADR.
- Extend `DockerWorkspaceSourceRequest` with
  `runtime_profile: str | None`.
- Extend `coding_agent.remote.client.create_remote_session` and the CLI
  `remote run` / `remote repl` commands with `--runtime <profile>`.
- Pass the selected runtime profile through
  `workspace_source.runtime_profile`.
- Add `_load_runtime_profiles_config()` in `src/coding_agent/ui/http_server.py`
  and merge it into the cloud workspace config passed to
  `provision_cloud_binding_from_config`.
- Keep existing clients compatible when no runtime profile is present.

### PR 2: Docker provider profile resolution

- Extend `src/coding_agent/environment/docker_workspace_provider.py` to read
  a server-internal `runtime_profiles` map from the cloud workspace config.
- When `source.runtime_profile` is set:
  - require that profile to exist;
  - require `provider = "docker"`;
  - use the profile's `image`, `network`, `cpus`, `memory`, `pids_limit`, and
    `exec_user` values for that provisioned workspace;
  - reject the profile image if it is not in the effective image allowlist.
- Do not let source payloads override image, network, user, or resource fields
  directly.
- Preserve old `[cloud_workspace].image` behavior when no profile is requested.

### PR 3: Documentation and dogfood guidance

- Update `docs/remote-sandbox-production.md` with `runtime_profiles` examples.
- Clarify that `python:3.11-slim` is a smoke-test baseline and `universal` is
  the intended real-project dogfood profile.
- Add a note that `--runtime` selects a server allowlisted profile, not an
  arbitrary image.

## Acceptance Criteria

- [x] Unit tests prove `remote run --runtime universal` sends
  `workspace_source.runtime_profile = "universal"`.
- [x] HTTP tests prove server config loading passes `[runtime_profiles]` into the
  workspace provider config.
- [x] Docker provider tests prove a requested profile changes the docker run image
  and resource flags.
- [x] Docker provider tests prove unknown profiles and non-Docker profiles fail
  before `docker run`.
- [x] Existing tests for no-profile Docker workspaces continue to pass unchanged.
- [x] `uv run pytest tests/coding_agent/environment/ tests/ui/test_http_server.py tests/cli/test_remote_client.py -q`
  passes.
- [x] `uv run basedpyright --level error` on changed Python files reports no errors.
- [x] `uv run ruff format --check` on changed Python files passes.

## References

- `src/coding_agent/environment/docker_workspace_provider.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/schemas.py`
- `src/coding_agent/remote/client.py`
- `src/coding_agent/__main__.py`
- `tests/coding_agent/environment/test_docker_workspace_provider.py`
- `tests/ui/test_http_server.py`
- `tests/cli/test_remote_client.py`
- `docs/remote-sandbox-production.md`
