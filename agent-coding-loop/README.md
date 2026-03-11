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
cd "$(git rev-parse --show-toplevel)"
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

Default runtime uses Eino orchestration for:

- `Coder` orchestration (`internal/agent/coder_eino.go`)
- `Reviewer` orchestration (`internal/agent/reviewer_eino.go`)
- Agent tools (`internal/tools/eino_tools.go`) including:
  - `repo_list`, `repo_read`, `repo_search`
  - `git_diff`
  - `run_command` (coder only)
  - `list_skills`, `view_skill`
- Loop engine orchestration (`internal/loop/engine_eino.go`) with:
  - `compose.Graph` turn loop (`turn -> branch -> turn/finish/failed/blocked`)
  - checkpoint wiring via `compose.WithCheckPointStore` + `compose.WithCheckPointID(runID)`

Build and run:

```bash
go get github.com/cloudwego/eino github.com/cloudwego/eino-ext/components/model/openai
go build ./cmd/agent-loop
```

## Git commit message limit

To enforce a short commit subject line (<= 50 words) locally, enable repository hooks:

```bash
git config core.hooksPath .githooks
```
