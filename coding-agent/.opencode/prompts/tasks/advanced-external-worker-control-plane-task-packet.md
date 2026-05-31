# Advanced External Worker Control Plane Foundations Task Packet

## Goal

Implement the first advanced external-worker control-plane slice for worker
process metadata, durable approval interaction management, and workspace-sync
extension metadata.

## Scope

- Persist optional worker instance/process/capability/workspace-sync metadata
  from external-worker claim and heartbeat requests.
- Expose that metadata through worker status APIs and CLI output.
- Add durable runtime interaction list/inspect/resolve HTTP APIs.
- Add remote client and CLI commands for interaction list/inspect/resolve.
- Reuse the existing session approval path for interaction resolution.

## Out of Scope

- Restoring an already-running local process after reconnect.
- Cross-machine workspace file synchronization.
- A dedicated worker registry table.
- Worker pool management UI.

## ADRs

- `docs/adr/0051-external-worker-execution-control-plane.md`
- `docs/adr/0052-external-worker-usable-control-plane.md`
- `docs/adr/0053-advanced-external-worker-control-plane-foundations.md`

## Target Tests

```bash
uv run pytest tests/ui/test_http_server.py -k "external_worker or interactions" -v
uv run pytest tests/cli/test_remote_client.py -k "remote_interaction or remote_worker or external_worker" -v
uv run pytest tests/coding_agent/test_pg_runtime_store.py -v
uv run ruff check docs/adr/0053-advanced-external-worker-control-plane-foundations.md src/coding_agent/server/http_server.py src/coding_agent/server/session_manager.py src/coding_agent/server/schemas.py src/coding_agent/remote/client.py src/coding_agent/remote/worker.py src/coding_agent/cli/remote_commands.py tests/ui/test_http_server.py tests/cli/test_remote_client.py tests/coding_agent/test_pg_runtime_store.py
```

## Stop Conditions

- Stop if process restoration requires supervising or attaching to an existing
  local process.
- Stop if workspace sync requires a new file transport protocol.
- Stop if approval resolution cannot reuse the existing session approval flow.
