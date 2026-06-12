# Web UI Deployment Baseline

This guide covers the single-image baseline for serving the React web UI and
the HTTP API from one Coding Agent process.

## Image

The production Docker image builds `webui/app` in a Node stage and copies the
Vite `dist/` output into the runtime image at `/app/webui-dist`.

The runtime image sets:

```bash
WEBUI_DIST_DIR=/app/webui-dist
```

When `WEBUI_DIST_DIR` is set, the FastAPI server mounts that directory at `/`
with `html=True`. API and `/console/*` routes are registered before the static
mount, so API routes keep priority. When `WEBUI_DIST_DIR` is unset, no static
UI is mounted; this preserves local development and tests.

## Required Public Settings

Do not expose a non-localhost listener without bearer auth. `coding-agent serve`
refuses to start on hosts such as `0.0.0.0` unless `[server]` config provides a
direct token or an environment-backed token:

```toml
[server]
host = "0.0.0.0"
port = 8080
production = true
bearer_token_env = "CODING_AGENT_BEARER_TOKEN"
```

Set the secret out of band:

```bash
export CODING_AGENT_BEARER_TOKEN="..."
```

Production mode also requires an explicit CORS allowlist:

```bash
export CODING_AGENT_CORS_ORIGINS="https://agent.example.com"
```

Multiple origins are comma-separated. `*` is accepted only for development
mode, not for `server.production = true`.

## TLS

Terminate TLS at a host-level reverse proxy such as Caddy and proxy to the
cluster service or local process on port `8080`. Keep Coding Agent auth enabled
even when the proxy is private.

## Smoke

1. Start the server with a production config, bearer token env, and CORS env.
2. Open the HTTPS domain and load the web UI.
3. Send API requests with `Authorization: Bearer $CODING_AGENT_BEARER_TOKEN`.
4. Verify `/healthz` and `/readyz`.
5. Create a session, run an ask-mode approval flow, reload the browser, restore
   the session from the list, and continue with a new prompt.

## Follow-Ups

This repository snapshot does not contain the Helm chart files, so
`values-prod.yaml`, PostgreSQL PVC/secret wiring, and k3s deployment values are
tracked as the next deployment slice once the chart source is available.
