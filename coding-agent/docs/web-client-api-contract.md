# Web Client API Contract

The contract a GUI client (web / desktop / mobile) consumes from the agent
server. This is the **stable contract**: today it is served by the Python FastAPI
server (`coding_agent.server.http_server`); a future Go runtime must honour the
same shapes so clients survive the rewrite untouched.

Source of truth in code:
- Routes & SSE mapping: `src/coding_agent/server/http_server.py`
- Request/response schemas: `src/coding_agent/server/schemas.py`
- User-facing display events: `src/coding_agent/events/display.py`
- Auth: `src/coding_agent/server/auth.py`

## Transport & auth

- Base URL: server default is `http://127.0.0.1:8080` (`coding_agent serve`).
- Auth header: `X-API-Key: <token>` (also accepts `Authorization: Bearer <token>`).
  Sent on every request including the streaming POST.
- Streaming is **SSE over `POST`** (`Content-Type: text/event-stream` response).
  ⚠️ The browser's native `EventSource` only does `GET`, so it **cannot** be used
  for `/prompt` or `/resume`. Clients must read the response body as a stream
  (`fetch` + `ReadableStream`, or a lib like `@microsoft/fetch-event-source`).

## REST endpoints (GUI MVP)

| Method | Path | Body → Response |
|---|---|---|
| `POST` | `/sessions` | `CreateSessionRequest` → `{ session_id }` |
| `GET`  | `/sessions` | → `{ sessions: SessionSummary[] }` |
| `GET`  | `/sessions/{id}` | → `SessionSummary` |
| `GET`  | `/sessions/{id}/runs` | → `{ session_id, runs: RuntimeRun[] }` |
| `POST` | `/sessions/{id}/prompt?event_format=display` | `{ prompt }` → **DisplayEvent SSE stream** |
| `POST` | `/sessions/{id}/resume?event_format=display` | `{ prompt?, resume_reason }` → **DisplayEvent SSE stream** |
| `POST` | `/sessions/{id}/approve` | `{ request_id, approved, feedback?, scope: "once"\|"session" }` → `{ status, request_id, decision }` |
| `POST` | `/sessions/{id}/cancel` | → `{ session_id, turn_id, status }` |
| `GET`  | `/sessions/{id}/display-events` | → live reconnect **DisplayEvent SSE stream** |
| `GET`  | `/runs/{run_id}/display-events` | → replayed `DisplayEvent[]` |
| `GET`  | `/sessions/{id}/result` | → `SessionResultResponse` (`final_answer`, ...) |
| `GET`  | `/sessions/{id}/memory/reviews?status=` | → `MemoryReviewRecordResponse[]` |
| `POST` | `/sessions/{id}/memory/reviews/{candidate_id}` | `{ status, reason? }` → `MemoryReviewTransitionResponse` |
| `GET`  | `/sessions/{id}/memory/semantic/status` | → `SemanticMemoryStatusResponse` |
| `POST` | `/sessions/{id}/memory/semantic/rebuild` | `{ batch_size, allow_rebuild, confirm_global: true }` → `SemanticMemoryRebuildResponse` |
| `GET`  | `/sessions/{id}/workspace/diff` | → `{ files[], additions, deletions }` |
| `GET`  | `/sessions/{id}/workspace/patch` | → `{ format: "unified_diff", patch }` |
| `GET`  | `/providers/{provider}/models` | → `ProviderModelsResponse` |
| `POST` | `/oauth/codex/start` | `{ label? }` → `CodexOAuthStartResponse` |
| `GET`  | `/oauth/codex/flows` | → `CodexOAuthFlowListResponse` |
| `GET`  | `/oauth/codex/flows/{flow_id}` | → `CodexOAuthFlowResponse` |
| `POST` | `/oauth/codex/flows/{flow_id}/cancel` | → `CodexOAuthFlowResponse` |
| `GET`  | `/oauth/accounts` | → `CodexOAuthAccountListResponse` |
| `DELETE` | `/oauth/accounts/{provider_key}` | → `{ status, provider }` |
| `GET`  | `/healthz` | → `{ status, sessions, version }` |

`CreateSessionRequest` (all optional unless noted):
`repo_path`, `approval_policy` (`auto` \| `interactive` \| `yolo`, default `auto`),
`provider`, `model`, `base_url`, `max_steps`, `run_target`,
`default_run_target`, `workspace_source`.

`provider` accepts the `ProviderName` literals plus `codex:<label>` for a named
codex account (label must match `^[a-z0-9][a-z0-9-]{0,30}$`). Creating a session
with an unconnected `codex:<label>` fails with `400` and points at
`POST /oauth/codex/start`.

`repo_path` is a shortcut for a local `default_run_target`. New clients must not
send `execution_binding`; the server rejects it. Stored legacy session payloads
that still contain `execution_binding` are migrated server-side into
`default_run_target` and are not returned as session state.

`409 Turn already in progress` is returned if you POST `/prompt` while a turn is
streaming. `404` = session not found (or not visible to this auth context).

## Memory review endpoints

Reviewed-memory candidates are curated per session through these endpoints. The
two `semantic/*` maintenance endpoints additionally require an admin auth
context.

- `GET /sessions/{id}/memory/reviews?status=candidate|accepted|rejected|archived`
  lists review records visible to the session (`status` query param optional).
  `MemoryReviewRecordResponse`: `candidate_id`, `status`, `review_reason?`,
  `kind`, `title`, `summary`, `scope`, `tags[]`, `confidence`, `topic_id?`,
  `session_id?`, `tape_id?`.
- `POST /sessions/{id}/memory/reviews/{candidate_id}` transitions one candidate.
  Body: `{ status: "accepted"|"rejected"|"archived", reason? }` →
  `MemoryReviewTransitionResponse` (`candidate_id`, `status`, `review_reason?`,
  `kind`, `title`, `scope`, `tags[]`, `confidence`). `400` on an invalid
  transition, `404` on an unknown candidate.
- `GET /sessions/{id}/memory/semantic/status` →
  `SemanticMemoryStatusResponse` (`document_count`, `reviewed_memory_count`,
  `accepted_reviewed_memory_count`, `topic_store_available`).
- `POST /sessions/{id}/memory/semantic/rebuild` reindexes the global semantic
  backend. Body: `{ batch_size: int, allow_rebuild: bool,
  confirm_global: true }` (`confirm_global` must be `true`; the rebuild is
  global, not per-session) → `SemanticMemoryRebuildResponse` (`scope`,
  `topic_count`, `reviewed_memory_count`, `indexed_count`, `skipped_count`,
  `deleted_count`, `indexed_ids[]`, `deleted_ids[]`).

## Codex OAuth login & multi-account

The server can run codex device-code logins headlessly and keep multiple
ChatGPT accounts connected side by side. Storage keys: `codex` (default
account, written by the CLI login) and `codex:<label>` (named accounts).

- `POST /oauth/codex/start` — body `{ label? }`; requests a device code and
  starts a background poll → `CodexOAuthStartResponse`
  (`flow_id`, `verification_url`, `user_code`, `expires_in`). `502` when the
  device-code request to OpenAI fails (no flow is created). `label` must match
  `^[a-z0-9][a-z0-9-]{0,30}$`; when omitted, the label is derived from the
  id_token after login (email first, then a short `chatgpt_account_id` code,
  with `-2`/`-3`... appended on conflicts). Logging in again with the same
  explicit label overwrites that account's record.
- `GET /oauth/codex/flows` — all in-flight and recently finished flows.
  Flows are in-memory only: a server restart clears them; finished flows stay
  queryable for the flow TTL (10 minutes).
- `GET /oauth/codex/flows/{flow_id}` — `CodexOAuthFlowResponse`:
  `state` is `pending` \| `authorized` \| `error` \| `expired` \| `cancelled`;
  `verification_url`/`user_code` are always present, `account_label` is set
  once `authorized`, `error` carries the failure message on `error`.
  Poll timeout / no user authorization → `expired` (client may retry);
  token-exchange failure → `error`. `404` for unknown flow ids.
- `POST /oauth/codex/flows/{flow_id}/cancel` — aborts polling and marks the
  flow `cancelled`; `404` for unknown flow ids.
- `GET /oauth/accounts` — connected codex accounts:
  `{ accounts: [{ provider, label, email?, plan?, connected_at }] }` where
  `provider` is `codex` or `codex:<label>` and the default account's `label`
  is `"default"`.
- `DELETE /oauth/accounts/{provider_key}` — deletes the local record only
  (**no remote revoke**; use the CLI `oauth logout codex --revoke` or the
  OpenAI account settings page for a full cleanup). `404` when the key is not
  connected, `400` for non-codex keys.

Clients pick an account at session creation by sending
`provider: "codex:<label>"`; `provider: "codex"` uses the default account.

## Provider model listing

`GET /providers/{provider}/models` returns the live model list for a provider,
for populating a model picker in the client. `provider` must be one of the
`ProviderName` literals (`openai`, `anthropic`, `copilot`, `kimi`, `kimi-code`,
`kimi-code-anthropic`, `deepseek`, `stepfun`, `codex`) or a multi-account
`codex:<label>` key (same rules as session creation); anything else is a
`422`.

`ProviderModelsResponse`: `{ provider, models: [{ id }], source }` where
`source` is `"live"` when the provider's live listing succeeded and
`"unavailable"` otherwise. The endpoint **never fails on provider-side
problems** — missing API key, network error, listing timeout (10s), or a
provider without a live listing API (anthropic, kimi-code-anthropic, copilot)
all return `200` with `{ models: [], source: "unavailable" }`. Clients should
treat `"unavailable"` as "fall back to local presets", not as an error.

OpenAI-compatible providers list via their `GET /models` endpoint. Codex
providers (`codex`, `codex:<label>`) list via the ChatGPT-backed
`GET /backend-api/codex/models?client_version=0.0.0`, filtered to
`visibility == "list"` entries and sorted by ascending `priority`; a missing
or expired OAuth account surfaces as `"unavailable"`.

Listing reuses the same provider construction as session runs
(`LLMProviderPlugin.provide_llm`), including env-var API-key fallbacks
(`KIMI_CODE_API_KEY`, `MOONSHOT_API_KEY`, `DEEPSEEK_API_KEY`, `STEP_API_KEY`),
so a provider that works for sessions also works here.

## Run metadata context pack

`GET /sessions/{id}/runs` returns `RuntimeRun[]`; each run's `metadata` dict may
carry a `context_pack` key describing the recall grounding (semantic memory +
KB) that was injected for that turn. Shape (same as
`ContextPack.to_dict()` in `src/coding_agent/topics/context_pack.py`):

```json
{
  "title": "Context Pack",
  "sections": [
    {
      "title": "Cross-topic recall references",
      "items": [
        {
          "source_kind": "topic_summary",
          "source_id": "topic:topic-auth",
          "label": "Auth recall",
          "body": "...",
          "rank": 1,
          "score": 0.47,
          "score_scale": "similarity",
          "repo_path": "src/auth.py",
          "line_start": 10,
          "line_end": 20,
          "evidence": [{"kind": "topic", "source_id": "topic-auth", "label": "..."}],
          "metadata": {}
        }
      ]
    }
  ]
}
```

Only `source_kind`, `source_id`, `label`, and `evidence` are always present on
an item; all other fields are optional. The key is absent entirely on turns
with no recall hits — treat a missing `context_pack` as "no grounding
recorded", not as an error.

## DisplayEvent SSE event types

Display streams are requested with `event_format=display`. Each SSE frame uses
`event:` as the `display_kind` and a JSON `data:` envelope:

```json
{
  "source_event_id": "event-123",
  "run_id": "run-123",
  "sequence": 42,
  "display_kind": "assistant_text_delta",
  "payload": {},
  "created_at": "2026-01-02T03:04:05+00:00"
}
```

Payloads that include `agent_id` use `agent_id == ""` for the root agent and a
non-empty `agent_id` for subagents. Render subagent events separately; only a
root `final_result` closes the prompt stream.

| `event` | Key `data` fields | Client action |
|---|---|---|
| `assistant_text_delta` | envelope `payload.content`, `payload.role`, `payload.agent_id` | append text to the current assistant message |
| `thinking_delta` | `payload.text`, `payload.agent_id` | append to a collapsible reasoning panel |
| `progress_update` | `payload.phase`, `elapsed_seconds`, `tokens_in`, `tokens_out`, `model_name`, `context_percent` | update status bar |
| `tool_call` | `payload.tool_name`, `payload.arguments`, `payload.call_id` | render a tool-call card keyed by `call_id` |
| `tool_result` | `payload.call_id`, `payload.tool_name`, `payload.display_result`, `payload.is_error` | attach redacted display result to the matching card |
| `approval_prompt` | `payload.request_id`, `payload.tool_call{tool_name,arguments,call_id}`, `payload.timeout_seconds` | show approve/deny UI → `POST /approve` with `request_id` |
| `approval_result` | `payload.request_id`, `payload.approved`, `payload.feedback` | echo / clear the prompt |
| `final_result` | `payload.turn_id`, `payload.completion_status`, `payload.agent_id` | close the root turn when `agent_id` is empty |
| `ErrorMessage` / `Error` | `content` / `error` | show error |

### Stream termination

The stream ends when a `final_result` arrives **with an empty `payload.agent_id`**
(the root turn finished). `final_result` frames carrying a non-empty
`payload.agent_id` are subagent turn boundaries and do **not** close the stream.

## Minimal client loop

1. `POST /sessions` → keep `session_id`.
2. `POST /sessions/{id}/prompt?event_format=display {prompt}`, read the body as
   an SSE stream.
3. For each frame, switch on `event`:
   - accumulate `assistant_text_delta.payload.content` into the visible answer,
   - render tool cards from `tool_call` / `tool_result`,
   - on `approval_prompt`, block on user choice and `POST /approve`.
4. Stop when `final_result` has empty `payload.agent_id`.
5. Optionally `GET /sessions/{id}/workspace/diff` to show file changes.

## History restore and reconnect

Full history restore is deterministic replay:

1. `GET /sessions/{id}` for current status and resume metadata.
2. `GET /sessions/{id}/runs` for durable runs in session order.
3. For each run, `GET /runs/{run_id}/display-events`.
4. Fold the replayed `DisplayEvent` records through the same reducer used for
   live prompt streams.

`GET /sessions/{id}/display-events` is not the full-history replay endpoint. It
is a live session-level SSE stream for reconnecting while a turn is still active
after the client has replayed persisted history.

## Notes for the future Go runtime

- Display event names and envelope field names above are the client contract.
- SSE-over-POST + `X-API-Key` is the wire format clients depend on.
- The root-turn termination rule (`final_result` with empty `payload.agent_id`)
  must hold.
- `call_id` correlates `tool_call` ↔ `tool_result`; `request_id` correlates
  `approval_prompt` ↔ `/approve`.
- Legacy wire streams remain available without `event_format=display`, but new
  GUI clients should consume `DisplayEvent` streams.
