# Task Packet: Raft Host Runtime + Coding Agent Dev Container

Baseline:
- Repository: `delete-cloud/Coding-Agent-Loop`
- Base branch: `origin/main`
- Base SHA: `3d1585391e19b408d97c0b3b99ef6fda4c2ef935`

Goal:
- Add a version-pinned, rebuildable development container for `coding-agent` so a Raft-managed
  Codex runtime may edit a host checkout while all dependency installation,
  build, lint, typecheck, and test commands run inside the container.
- Do not claim bit-for-bit reproducibility: Debian packages are resolved from
  the configured `apt` repositories at image build time.

Scope:
- Add `coding-agent/.devcontainer/devcontainer.json`.
- Add the minimal Dockerfile and container verification script under
  `coding-agent/.devcontainer/`.
- Update `coding-agent/README.md` with the supported workflow, trust boundary,
  fixed tool versions, and verification commands.
- Use Python 3.12.11, Node.js 20.19.5, pnpm 10.23.0, `uv` 0.12.1, and Ruff
  0.15.12.

Out of scope:
- Installing packages, cloning repositories, or starting containers on vm-23.
- SSH, Forge API, push, PR, CI, Argo CD, Kubernetes, DNS, NetBird, ingress,
  firewall, relay, backup, or other live operations.
- Installing or integrating Paseo.
- Running Raft Computer or an agent runtime inside the dev container.
- Giving the dev container the host Docker socket or implementing real Docker
  workspace-provider E2E inside the container.
- Fixing unrelated application, WebUI, pnpm metadata, or test defects.

Context:
- The Raft runtime remains on the host. The dev container is a version-pinned,
  rebuildable dependency and command environment, not a host security boundary
  or a bit-for-bit reproducible image.
- vm-23 runs K3s through cri-dockerd. Its system Docker daemon and
  `/run/docker.sock` must not be mounted into or exposed to the dev container.
- A future vm-23 canary must use an independently reviewed rootless
  Docker/Podman endpoint and is a separate live-ops authorization gate.
- vm-23 has limited headroom and no swap, so the canary must be bounded to one
  active workspace with 2 CPUs, 1536 MiB memory, and 512 PIDs.
- Agent-provider credentials remain in the Raft host runtime and must not be
  copied into the image or mounted separately into the dev container. The
  complete host checkout is visible through the workspace bind mount, so
  credentials must remain outside that checkout.
- The Docker build context must be limited to `coding-agent/.devcontainer/`,
  not the complete host checkout.
- Pin the multi-platform OCI index digests for all base stages:
  - `node:20.19.5-bookworm-slim@sha256:9e70124bd00f47dd023e349cd587132ae61892acc0e47ed641416c3e18f401c3`
  - `python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7`
  - `ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded`
  - `ghcr.io/astral-sh/ruff:0.15.12@sha256:e42f3866bb9f701dbc459cdec3f5aca06511e6a38e6e04bfd4d04a2be26d4fd4`
- The repository's existing production `Dockerfile` is not a development
  environment and must remain unchanged.
- No ADR is required because this change does not alter persistence, protocol,
  data models, runtime ownership, or application architecture.

Acceptance Criteria:
- A clean checkout can be opened with the Dev Containers CLI using only files
  committed under `.devcontainer/`.
- Inside the container, `python`, `node`, `pnpm`, `uv`, and `ruff` report exactly
  3.12.11, 20.19.5, 10.23.0, 0.12.1, and 0.15.12 respectively.
- The container runs as a non-root development user.
- The configuration does not request privileged mode, host networking, or any
  Docker/Podman socket mount.
- The container has explicit CPU, memory, and PID limits suitable for the
  single-workspace vm-23 canary.
- Dependency setup is fail-fast and uses `uv sync --all-extras` plus
  `pnpm install --frozen-lockfile` with pnpm 10.23.0.
- pnpm's content-addressed store resolves under the non-root user's home, not
  inside the host-mounted checkout.
- One documented verification entrypoint runs concrete Python and WebUI gates
  inside the container and propagates any failure.
- The workflow does not copy or separately mount Codex, Claude, Raft, Forgejo,
  or other credentials into the container, and warns that all files stored in
  the bound checkout are visible inside it.
- README documentation states that real Docker workspace-provider E2E and
  Paseo integration are deferred.

Allowed files:
- `coding-agent/.devcontainer/**`
- `coding-agent/README.md`
- `coding-agent/.opencode/task-packets/2026-08-07-vm23-raft-devcontainer.md`

Do not touch:
- Application source under `coding-agent/src/`.
- Existing tests, lockfiles, production Dockerfile, CI, GitOps, SRE inventory,
  generated files, or secrets.

Target tests:
- `uv run pytest tests/cli -q`
- `corepack pnpm@10.23.0 --dir webui/app test`
- `corepack pnpm@10.23.0 --dir webui/app typecheck`
- `corepack pnpm@10.23.0 --dir webui/app build`
- `.devcontainer/verify.sh`

Container verification:
- Build/open the container with Dev Containers CLI 0.88.0.
- Execute `.devcontainer/verify.sh` inside the running container.
- Start from the exact baseline plus candidate diff in a fresh clone with no
  `.venv`, `node_modules`, or package-manager store, and complete dependency
  installation plus all target tests under the 1536 MiB limit.
- Inspect the effective configuration and running container to prove the
  non-root user, resource limits, lack of privileged/host-network mode, and
  lack of Docker/Podman socket mounts.
- Inspect container state after verification and require `OOMKilled=false` and
  a successful command/container state.
- Remove only the task-owned local verification container after evidence is
  captured. Do not run broad Docker cleanup or prune commands.

Handoff:
- Return the implementation summary, exact changed files, exact commands and
  outputs, remaining unverified items, and risks.
- Edits only. Do not commit, push, create a PR, call Forge APIs, use SSH,
  access vm-23, run kubectl/Argo, or perform any live operation.

Loop policy:
- Engineer implements the smallest correct change and runs target tests that
  are available without live infrastructure.
- Reviewer reviews only the resulting diff, task packet, and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns affected tests.
- Verifier reruns the authoritative gates and reports evidence.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architecture redirection, a need for live vm-23 access, or any file
  outside the allowlist to the human.
- Do not weaken the socket, credential, resource, or live-ops boundaries to
  make a test pass.
