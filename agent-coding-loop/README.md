# Agent Coding Loop

A local coding loop runtime that executes:

`goal -> coder -> tests/checks -> reviewer -> commit/pr (live or dry-run)`

## Features

- CLI commands:
  - `agent-loop run --goal "..."`
  - `agent-loop serve --listen 127.0.0.1:8787`
  - `agent-loop resume --run-id <id>`
  - `agent-loop inspect --run-id <id>`
- HTTP API:
  - `POST /v1/runs`
  - `GET /v1/runs/{id}`
  - `GET /v1/runs/{id}/events`
  - `POST /v1/runs/{id}/resume`
  - `GET /v1/skills`
  - `GET /v1/skills/{name}`
- SQLite persistence (via local `sqlite3` binary)
- Skills discovery and on-demand loading (`SKILL.md`)
- PR mode: `auto`, `live`, `dry_run`

## Build

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop
go test ./...
go build ./cmd/agent-loop
```

## Run

```bash
./agent-loop run --goal "add health endpoint" --repo /path/to/repo --pr-mode auto
```

## Config

- Optional JSON config: `--config config.json`
- Env overrides:
  - `AGENT_LOOP_LISTEN`
  - `AGENT_LOOP_DB_PATH`
  - `AGENT_LOOP_ARTIFACTS_DIR`
  - `OPENAI_BASE_URL`
  - `OPENAI_MODEL`
  - `OPENAI_API_KEY`

## Eino Integration

Default build uses a stdlib OpenAI-compatible client (`internal/agent/client.go`) for offline portability.

An Eino-backed adapter is included behind build tag `eino`:

```bash
go build -tags eino ./cmd/agent-loop
```

When using `-tags eino`, add Eino dependencies in `go.mod` and make sure your environment can download modules.
