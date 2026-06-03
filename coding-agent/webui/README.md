# Coding Agent — Web Client

A thin GUI client over the agent's HTTP+SSE API. Decoupled from the runtime
language: it speaks only the network contract in
[`docs/web-client-api-contract.md`](../docs/web-client-api-contract.md), so the
backend can later be ported (Python → Go) without touching the frontend.

## Two artifacts

| Path | What | When |
|---|---|---|
| `index.html` | Zero-dependency standalone client (single file, no build) | Quick checks; open directly in a browser |
| `app/` | React + Vite + TypeScript app (Tauri-ready for desktop/mobile) | The real client |

## Run

Start the agent server (provides the API + SSE):

```bash
coding-agent serve --port 8765
```

### Standalone (no build)

Open `webui/index.html` in a browser, set the base URL to `http://127.0.0.1:8765`
(CORS is `*` in dev), then **New session** → type a prompt.

### React app

```bash
cd webui/app
pnpm install      # or npm install
pnpm dev          # http://localhost:5173
```

Config (base URL, API key, repo path, approval policy) persists in localStorage.

## Notes

- Streaming is **SSE over POST**, so the native `EventSource` can't be used; the
  client reads the `fetch` body stream and parses CRLF-delimited SSE frames
  (`src/lib/sse.ts`).
- Prompt streams request `event_format=display` and render the user-facing
  `DisplayEvent` envelope instead of raw internal wire events.
- The stream ends on the root `final_result` (empty `agent_id`); subagent turn
  boundaries are ignored.
- A failing turn with `completion_status: "error"` usually means the server has
  no LLM provider configured — that is a backend config issue, not the client.

## Future (when needed)

- Wrap `app/` in Tauri 2.0 for desktop + mobile builds (reuses this same bundle).
- Push notifications (the main reason to ship a native mobile app).
- Reconnect/replay via `GET /sessions/{id}/display-events`; markdown + diff
  rendering.
