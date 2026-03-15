# Progress Visibility And Streaming Design

Status: proposed
Last updated: 2026-03-15

## Goal

Add a dedicated progress event stream so CLI and HTTP consumers can observe run lifecycle in near real time without changing the existing audit tables or breaking the current `/v1/runs/{id}/events` contract in the first rollout.

## Scope

This slice adds:

- a new `progress_events` SQLite table for user-facing progress consumption
- structured progress emission from the loop engine
- a REST incremental progress endpoint
- an SSE streaming endpoint
- local CLI progress tailing for `run` and `resume`

This slice does not add:

- a TUI
- WebSockets
- live tool stdout streaming
- migration of `steps`, `tool_calls`, `reviews`, or `artifacts` into the progress stream
- changes to the existing `/v1/runs/{id}/events` endpoint in the first rollout
- a new `ResumeWithProgress` service method

## Core Decisions

### 1. Progress events get their own table

`steps`, `tool_calls`, `reviews`, and `artifacts` remain audit and diagnosis tables.

`progress_events` is a separate consumption-oriented stream. This keeps real-time UX concerns isolated from durable audit storage and avoids forcing one schema to serve both purposes badly.

### 2. Cursoring uses global `id`, not per-run `seq`

The progress stream uses:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`

Consumers track `last_seen_id` and incrementally read with:

```sql
WHERE run_id = ? AND id > ?
ORDER BY id ASC
LIMIT ?
```

Per-run `seq` is intentionally rejected. The current sqlite3 CLI process model does not provide a clean atomic `MAX(seq)+1` path without extra coordination, and the consumer does not need a per-run sequence that starts from 1.

### 3. `iteration` is never null

`iteration` is:

- `INTEGER NOT NULL DEFAULT 0`

Run-level events use `iteration = 0`. Iteration-level events use the actual loop iteration.

This avoids implicit null handling in sqlite3 CLI output parsing and keeps consumer semantics explicit.

### 4. Event writing happens only in the engine

Progress emission is owned by the loop engine. Tools, HTTP handlers, and store code do not synthesize progress events on their own.

The engine is the only layer that understands lifecycle boundaries such as:

- a patch apply failure that will retry
- a doom-loop block
- an iteration completion
- a terminal run completion vs failure vs blocked outcome

### 5. First rollout keeps `/events` unchanged

To avoid breaking existing scripts or tests, the first rollout does not change:

- `GET /v1/runs/{id}/events`

Instead it adds:

- `GET /v1/runs/{id}/progress`
- `GET /v1/runs/{id}/stream`

Both new endpoints read only from `progress_events`.

### 6. CLI progress goes to `stderr`

Real-time progress is printed to `stderr`.

Final structured `RunResult` stays on `stdout`.

This preserves existing shell usage such as:

```bash
agent-loop run ... | jq
```

### 7. `run` and `resume` are intentionally asymmetric

`run` needs a new service entrypoint because the CLI must know `run_id` before the run finishes.

Recommended API:

```go
func (s *Service) RunWithProgress(ctx context.Context, spec model.RunSpec) (string, <-chan model.RunResult, error)
```

`resume` does not need a matching service helper. The CLI already knows `run_id`, so it can:

- start a goroutine calling `svc.Resume(ctx, runID)`
- tail `progress_events` locally using that run ID
- wait on the local result channel

## Schema

Add a new table:

```sql
CREATE TABLE IF NOT EXISTS progress_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  iteration INTEGER NOT NULL DEFAULT 0,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_progress_events_run_id_id
  ON progress_events(run_id, id);
```

Field semantics:

- `id`: global cursor for REST incremental reads, SSE replay, and CLI tailing
- `run_id`: owning run
- `iteration`: `0` for run-level events, actual iteration for iteration-level events
- `event_type`: fixed lifecycle category
- `status`: fixed phase status
- `summary`: compact human-facing line
- `detail_json`: small machine-facing extension object
- `created_at`: wall-clock display timestamp

## Event Types

Phase 1 of progress visibility supports exactly these event types:

- `run_started`
- `iteration_started`
- `coder_generating`
- `patch_applying`
- `patch_failed`
- `command_running`
- `reviewer_reviewing`
- `iteration_completed`
- `run_completed`
- `run_failed`
- `run_blocked`

These are intentionally lifecycle-oriented rather than audit-oriented.

## Status Values

Status is fixed to:

- `started`
- `progress`
- `completed`
- `error`

`progress` is allowed but should be used sparingly in the first rollout. The first usable version should work primarily with `started`, `completed`, and `error`.

## Detail JSON Contract

`detail_json` is an escape hatch, not a second primary payload.

Allowed keys in the first rollout:

- `reason`
- `error`
- `decision`
- `command_kind`
- `command`
- `blocked_tool`
- `blocked_count`
- `branch`
- `commit`
- `pr_url`
- `artifacts_dir`

Recommended usage by event type:

- `run_started`: `{"reason":"fresh_run"|"resume"}`
- `iteration_started`: `{}`
- `coder_generating`: `{"reason":"initial"|"retry_after_review"}`
- `patch_applying`: `{}`
- `patch_failed`: `{"error":"git apply failed: ...","reason":"will_retry"}`
- `command_running`: `{"command_kind":"test"|"lint"|"build"|"custom","command":"..."}`
- `reviewer_reviewing`: `{"decision":"approve"|"request_changes"|"comment"}` on completion
- `iteration_completed`: `{"decision":"continue"|"request_changes"|"complete"|"abort"}`
- `run_completed`: `{"branch":"...","commit":"...","pr_url":"...","artifacts_dir":"..."}`
- `run_failed`: `{"error":"..."}`
- `run_blocked`: `{"reason":"doom_loop","blocked_tool":"run_command","blocked_count":3}`

Not included in `detail_json`:

- raw tool input or output
- patch text
- review findings blobs
- checkpoint internals
- model-specific diagnostic payloads
- command exit code in the first rollout

`exit_code` is intentionally deferred. The current runner path does not expose it cleanly without either changing the runner interface or explicitly unpacking `exec.ExitError`. First rollout keeps only the summarized `error` text.

## Engine Write Path

Add a single helper on the engine side:

```go
emitProgress(ctx, runID, iteration, eventType, status, summary, detail)
```

This helper should write directly into `progress_events` through the store.

Recommended emission points:

- `run_started`: immediately after the run enters `running`
- `iteration_started`: at the beginning of each turn
- `coder_generating started/completed`: before and after coder generation
- `patch_applying started`: before patch application begins
- `patch_failed error`: when patch application fails but the loop continues
- `command_running started/completed/error`: around command execution
- `reviewer_reviewing started/completed`: around reviewer invocation
- `iteration_completed completed`: once one loop iteration ends with a decision
- `run_completed completed`: final successful outcome
- `run_failed error`: final failed outcome
- `run_blocked error`: final blocked outcome, especially doom-loop detection

Important semantic boundaries:

- `patch_failed` does not terminate the run
- `run_blocked` is distinct from `run_failed`
- tools do not emit progress directly

## Service Contract

Add:

```go
func (s *Service) RunWithProgress(ctx context.Context, spec model.RunSpec) (string, <-chan model.RunResult, error)
```

Behavior:

1. validate spec
2. create the run record immediately and return `run_id`
3. start execution in a goroutine
4. deliver terminal `RunResult` through a buffered channel

This mirrors the current async creation pattern while replacing the in-memory `results` map handoff with a direct channel for CLI usage.

`resume` stays unchanged at the service layer.

## REST Incremental Endpoint

Add:

- `GET /v1/runs/{id}/progress?after_id=<id>&limit=<n>`

Query semantics:

- `after_id` defaults to `0`
- `limit` defaults to a conservative value such as `100`
- results are ordered by ascending `id`

Response shape:

```json
{
  "run_id": "run_123",
  "events": [
    {
      "id": 42,
      "run_id": "run_123",
      "iteration": 2,
      "event_type": "command_running",
      "status": "started",
      "summary": "running tests: go test ./...",
      "detail": {
        "command_kind": "test",
        "command": "go test ./..."
      },
      "created_at": 1742000000000
    }
  ],
  "next_after_id": 42
}
```

This endpoint is the polling-friendly machine contract.

## SSE Endpoint

Add:

- `GET /v1/runs/{id}/stream`

SSE rules:

- each event uses `progress_events.id` as SSE `id`
- event name is always `progress`
- event payload is the same structured event returned by `/progress`
- replay starts from `Last-Event-ID` when present, otherwise optional `after_id`
- send keepalive comments every `15s`
- after terminal run state and final buffered events are sent, close the stream

Example frame:

```text
id: 42
event: progress
data: {"id":42,"run_id":"run_123","iteration":2,"event_type":"command_running","status":"started","summary":"running tests: go test ./...","detail":{"command_kind":"test","command":"go test ./..."},"created_at":1742000000000}
```

## CLI Tail Contract

`run` and `resume` tail the local store directly instead of going through HTTP.

Behavior:

- poll `progress_events` every `1s`
- remember `last_seen_id`
- stop tailing on:
  - `run_completed`
  - `run_failed`
  - `run_blocked`
- enforce a default tail timeout of `30m`
- print progress to `stderr`
- print final `RunResult` JSON to `stdout`

For `run`:

- use `RunWithProgress(...)` to get `run_id` up front
- tail by `run_id`
- wait on the returned result channel for the final result

For `resume`:

- create a local goroutine that calls `svc.Resume(ctx, runID)`
- tail by the already-known `run_id`
- wait on the local result channel

This keeps the asymmetry explicit and avoids inventing an unnecessary `ResumeWithProgress(...)` helper.

## Rollout Plan

First rollout:

1. add `progress_events` schema and store APIs
2. add engine progress emission
3. add `/progress`
4. add `/stream`
5. add CLI local tailing

Do not change in first rollout:

- `GET /v1/runs/{id}/events`
- `Inspect(...)`
- existing audit tables
- command runner interface

Only after validating that no consumers depend on the current `/events` semantics should the project consider redirecting or deprecating that older endpoint.

## Testing Boundaries

### Store

- migration creates `progress_events`
- insert and list by `run_id + id`
- `iteration = 0` round-trips cleanly
- `detail_json` defaults to `'{}'`

### Engine

- success path emits expected lifecycle events
- patch apply failure emits `patch_failed` without ending the run
- doom-loop block emits `run_blocked`, not `run_failed`
- failed run emits `run_failed`

### HTTP

- `/progress` returns ordered incremental events with `next_after_id`
- `/stream` honors `Last-Event-ID` or `after_id`
- `/stream` sends keepalives
- `/stream` closes after terminal state

### CLI

- progress goes to `stderr`
- final result stays on `stdout`
- tail loop exits on terminal event
- tail loop times out cleanly
- `run` uses `RunWithProgress`
- `resume` uses a local goroutine + channel wrapper without a new service API

## Non-Goals

This design intentionally does not solve:

- richer interactive editing tools
- live token streaming from the model
- a full-screen terminal dashboard
- event-sourcing of all internal tables
- backward compatibility migration of `/events`
