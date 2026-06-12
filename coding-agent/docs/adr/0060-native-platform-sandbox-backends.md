# ADR-0060: Native platform sandbox backends and sandbox_mode convergence

**Status**: Accepted
**Date**: 2026-06-04

## Context

`bash_run` resolves command isolation through a single `sandbox_mode` selector
that maps one-to-one to a backend: `none`, `nsjail`, or `docker`
(`src/coding_agent/tools/sandbox.py`, `src/coding_agent/tools/shell.py`). This
couples the user-visible policy to a specific implementation and offers no
host-native isolation: `none` runs commands directly under host-execution
guards, `nsjail` is Linux-only and rarely deployed, and `docker` requires a
container runtime. On macOS there is no isolation option at all beyond `none`.

ADR-0058 established that sandbox is a wrapper policy applied to an executor
environment derived from `RunTarget.isolation`, not an environment type. This
ADR refines what the local sandbox policy can resolve to. We want platform-
native sandboxing that mirrors Codex's proven Linux and macOS implementations
(Seatbelt on macOS, a bubblewrap-based jail on Linux) without expanding the
configuration surface into a backend zoo, and we want to keep the existing
explicit container backends.

The guiding constraint from the design discussion is minimalism: copy the
shape of Codex's Linux/macOS sandboxes, keep Podman and Docker as explicit
container backends, and avoid introducing new user-visible knobs for every
underlying mechanism (Landlock, bubblewrap, Seatbelt).

## Decision

Converge the user-visible `sandbox_mode` to four values:

```text
sandbox_mode = none | native | podman | docker
```

`native` is a platform resolution, not a single backend. `build_sandbox()`
stays the single entry point and delegates native resolution to a dedicated
`_resolve_native(config)` helper so the platform mapping can be unit-tested in
isolation:

```python
def _resolve_native(config: SandboxConfig) -> SandboxRunner:
    ...

def build_sandbox(config: SandboxConfig) -> SandboxRunner:
    if config.mode == "native":
        return _resolve_native(config)
    ...
```

`_resolve_native` chooses the backend from the host OS:

- Darwin + `native` → macOS Seatbelt runner (`sandbox-exec` profile), Codex-style.
- Linux + `native` → Linux Codex-style runner, bubblewrap as the primary path.
  Landlock is an internal fallback/hardening detail only; it is not a separate
  user-visible mode.
- Windows + `native` → fail closed (`SandboxUnavailableError`).

`podman` and `docker` are explicit container backends. They are never selected
by `native`; the user opts into a container runtime deliberately. `PodmanSandboxRunner`
is added beside `DockerSandboxRunner`, reusing the same command-construction
shape (`--rm`, `--network none`, workspace bind mount, workdir = cwd,
no-new-privileges, rootless preferred).

`nsjail` is dropped from the allowed `sandbox_mode` values. Configs that set
`sandbox_mode = "nsjail"` are now rejected at validation. `NsjailSandboxRunner`
may remain in `sandbox.py` temporarily but is unreachable through config and is
slated for removal in a follow-up; it is not extended.

Native backends preserve the existing safety contract used by `none`/`docker`:
workspace is read-write, required system paths are read-only, network is denied
by default, `cwd` must resolve inside the workspace
(`_validate_cwd`), and env is explicit/controlled rather than inherited
wholesale. Scope is limited to the single-command `bash_run` path; broader
generalization is out of scope.

When a `native` backend's required binary is unavailable, runners fail closed
with `SandboxUnavailableError` — they must never silently downgrade to `none`.

The default config value initially stayed `sandbox_mode = "none"` while the
native backends and structured retry signal were staged. The final PR switches
the default to `native`.

## Implementation Status

Implemented. The user-visible `sandbox_mode` values are now converged to
`none`, `native`, `podman`, and `docker`; `nsjail` is rejected by validation.
Native sandbox resolution fails closed when the required platform backend is
unavailable, and the default is now `native`.

The default switch is staged behind a structured retry signal: when
`structured_results` is enabled, `bash_run` classifies native sandbox failures
as `sandbox_denied` or `sandbox_unavailable` with
`retry_hint = "request_unfenced_retry"`. This lets the model distinguish a
recoverable sandbox boundary from an ordinary command failure and request an
approved unfenced retry.

Verification is captured by `tests/tools/test_shell.py` and the acceptance
criteria below.

## Alternatives Rejected

- Expose `landlock`, `bwrap`, and `seatbelt` as separate user-visible modes —
  rejected because it bloats the configuration surface; the mechanism should be
  an implementation detail of `native`, resolved per platform.
- Extend `nsjail` into the primary Linux sandbox — rejected because it is
  legacy, has low adoption, and diverges from the Codex implementation we are
  intentionally copying.
- Keep `nsjail` as an accepted legacy `sandbox_mode` value — rejected in favor
  of dropping it from validation now; keeping a fourth half-supported backend in
  the user-visible surface contradicts the convergence goal. The runner class is
  left temporarily only to stage its removal.
- Silently fall back to `none` when a native backend binary is missing —
  rejected because it defeats the isolation contract; runners must fail closed.
- Flip the default to `native` in the same change that adds the backends —
  rejected to minimize risk; the default switch is staged as the last PR after
  native backends are stable.
- Add Windows native sandbox support now — rejected; Windows + `native` fails
  closed and native Windows isolation is out of scope.
- Build a generalized cross-platform isolation abstraction — rejected as
  over-engineering; we copy Codex's Linux/macOS shapes for the `bash_run`
  single-command case only.

## Acceptance Criteria

- [x] `test_native_mode_on_darwin_selects_macos_seatbelt_runner`
- [x] `test_native_mode_on_linux_selects_linux_bwrap_runner`
- [x] `test_native_mode_on_windows_fails_closed`
- [x] `test_linux_native_command_includes_network_off_workspace_bind_and_cwd`
- [x] `test_macos_native_builds_sandbox_exec_profile_with_workspace_rw_and_network_deny`
- [x] `test_podman_runner_builds_podman_run_with_network_none_and_workspace_bind`
- [x] Fail-closed when backend binary missing —
  `test_macos_native_fails_closed_when_sandbox_exec_missing`,
  `test_linux_native_fails_closed_when_bwrap_missing`,
  `test_podman_runner_fails_closed_when_binary_missing`
- [x] `test_nsjail_mode_is_rejected_by_validation`
- [x] `test_existing_none_and_docker_modes_unchanged`
- [x] `test_structured_result_marks_sandbox_denied`
- [x] `test_structured_result_marks_sandbox_unavailable`
- [x] `uv run pytest tests/tools/test_shell.py -k "native or podman or nsjail or sandbox" -v`
- [x] `uv run ruff check src/coding_agent/tools/sandbox.py src/coding_agent/tools/shell.py`

## References

- `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- `src/coding_agent/tools/sandbox.py`
- `src/coding_agent/tools/shell.py`
- `tests/tools/test_shell.py`
- Codex Linux sandbox (bubblewrap/Landlock) and macOS Seatbelt implementations — design reference
