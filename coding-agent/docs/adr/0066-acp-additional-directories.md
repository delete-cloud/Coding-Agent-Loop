# ADR-0066: ACP additional workspace directories

**Status**: Accepted
**Date**: 2026-06-05

## Context

ACP `session/new` and `session/load` include `additionalDirectories`: a complete
list of extra absolute workspace roots to activate for the session. Coding Agent
previously treated each local session as a single workspace root. Accepting the
ACP field without changing runtime access checks would make capability
negotiation misleading.

## Decision

Store ACP `additionalDirectories` as durable per-session metadata and pass them
to runtime creation as `additional_workspace_roots_override`.

The runtime exposes the extra roots through environment tool config as
`additional_workspace_roots`. Local file tools and `bash_run` treat an absolute
path as allowed when it is under either the primary workspace root or an
additional root. Relative paths still resolve against the primary workspace
root. Sandbox configs also carry the additional roots so native, Docker, and
Podman sandbox runners can bind or allow those roots.

`session/load` replaces the complete additional-root set from the request. If
the set changes, the current runtime adapter is closed so the next turn rebuilds
with the updated access boundary.

The ACP initialization response advertises
`sessionCapabilities.additionalDirectories`, and `session/list` returns each
session's `additionalDirectories` so clients can discover the active root set.

## Alternatives Rejected

- Parse and ignore `additionalDirectories` — rejected because ACP clients would
  expect those roots to be usable.
- Fold additional roots into the main `RunTarget` workspace — rejected because
  the primary workspace root remains the cwd and diff/export anchor.
- Allow additional roots only in prompt text — rejected because the protocol
  describes activated workspace roots, not just model-visible hints.

## Acceptance Criteria

- [x] ACP `session/new` validates and passes absolute `additionalDirectories`.
- [x] ACP `session/load` updates the complete additional-root set.
- [x] ACP `initialize` advertises `sessionCapabilities.additionalDirectories`.
- [x] ACP `session/list` returns per-session `additionalDirectories`.
- [x] Session records persist and restore additional directories.
- [x] Runtime creation passes additional roots to `create_agent`.
- [x] Local file tools allow absolute paths under additional roots and reject
      unrelated absolute paths.
- [x] `bash_run` allows cwd/path references under additional roots and rejects
      unrelated absolute paths.
- [x] Sandbox config carries additional roots to native/Docker/Podman runners.
- [x] `uv run pytest tests/acp -k "additional or compat" -v`
- [x] `uv run pytest tests/ui/test_session_persistence.py -k "additional_directories or session_record" -v`
- [x] `uv run pytest tests/coding_agent/test_runtime_preparation.py tests/coding_agent/test_bootstrap.py -k "additional_workspace_roots or runtime_preparation_service" -v`
- [x] `uv run pytest tests/coding_agent/tools/test_file_ops.py tests/tools/test_shell.py -k "additional or workspace" -v`
- [x] `uv run ruff check src/coding_agent/acp/server.py src/coding_agent/app.py src/coding_agent/environment/additional_roots.py src/coding_agent/executors/local_daemon.py src/coding_agent/runs/runtime_preparation.py src/coding_agent/server/session_manager.py src/coding_agent/tools/file_ops.py src/coding_agent/tools/file_patch_tool.py src/coding_agent/tools/shell.py src/coding_agent/tools/sandbox.py tests/acp/test_server.py tests/acp/test_compat_harness.py tests/coding_agent/test_bootstrap.py tests/coding_agent/test_runtime_preparation.py tests/coding_agent/tools/test_file_ops.py tests/coding_agent/tools/test_file_patch_tool.py tests/tools/test_shell.py tests/ui/test_session_persistence.py`

## References

- `docs/adr/0061-acp-stdio-adapter.md`
- `docs/adr/0065-acp-mcp-session-params.md`
- https://agentclientprotocol.com/protocol/schema
