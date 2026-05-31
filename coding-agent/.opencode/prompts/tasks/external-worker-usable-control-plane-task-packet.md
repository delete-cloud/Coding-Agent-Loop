# External Worker Usable Control Plane Task Packet

## Goal

Implement the first usable external-worker control-plane slice: inspect remote
sessions/runs/events, continue existing sessions, attach to events, and view
worker health.

## Scope

- Add HTTP session run listing and worker status endpoints.
- Add remote client helpers for runs, events, prompt continuation, attach, and
  workers.
- Add CLI commands for the same operations.
- Keep worker health derived from existing durable run metadata.

## Out of Scope

- Restoring an already-running local process after reconnect.
- Full approval inbox UI or queue management.
- Cross-machine workspace synchronization.
- Worker pool management UI.

## ADRs

- `docs/adr/0051-external-worker-execution-control-plane.md`
- `docs/adr/0052-external-worker-usable-control-plane.md`

## Target Tests

```bash
uv run pytest tests/ui/test_http_server.py -k "external_worker or sessions or runs or workers" -v
uv run pytest tests/cli/test_remote_client.py -k "remote_session or remote_run or remote_worker or external_worker or remote_prompt or remote_workers" -v
uv run pytest tests/coding_agent/test_pg_runtime_store.py -v
uv run ruff check src/coding_agent/server/http_server.py src/coding_agent/server/schemas.py src/coding_agent/remote/client.py src/coding_agent/cli/remote_commands.py tests/ui/test_http_server.py tests/cli/test_remote_client.py
```

## Stop Conditions

- Stop if the implementation requires a new persisted worker table.
- Stop if attach needs interactive TUI behavior beyond streaming current SSE
  events.
