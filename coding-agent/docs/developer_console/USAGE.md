# Developer Console Usage

The Developer Console is a read-only debug UI served by the existing Coding
Agent FastAPI app. Start the server normally:

```bash
uv run python -m coding_agent serve --host 127.0.0.1 --port 8080
```

Open:

```text
http://127.0.0.1:8080/console
```

## Pages

- `/console/sessions` lists recent visible sessions.
- `/console/runs` lists visible durable runtime runs and supports an optional
  `status` filter.
- `/console/runs/{run_id}` shows sanitized run metadata, message snapshot
  metadata, replayable runtime event metadata, and related debug links.
- `/console/interactions` shows pending and resolved HITL interactions.
- `/console/tape` shows tape info/search when a `TapeDebugStore` is available.
- `/console/context?run_id=...` shows context-pack evidence for a visible run.
- `/console/memory?run_id=...` shows memory evidence metadata for a visible run.
- `/console/actions?run_id=...` shows action, policy, patch-summary, and
  validation metadata for a visible run.
- `/console/observability?run_id=...` shows trace correlation metadata, metrics
  endpoint status, and safe Langfuse/Grafana links when configured.
- `/console/release` shows local health/readiness and release verification
  manifest gates.

## Privacy And Safety

Console pages do not execute actions, resolve approvals, apply patches, restore
checkpoints, or bypass policy. Pages render IDs, statuses, timestamps, counts,
safe labels, safe source paths, and manifest commands. They must not render raw
prompts, messages, model results, command output, stdout, stderr, env values,
file contents, patch contents, secrets, or credential-bearing URLs.

## Local Observability Links

For local Prometheus/Grafana, use the existing stack docs:

```text
docs/observability/LOCAL_STACK.md
```

Grafana and Langfuse links appear only when configured as safe HTTP(S) URLs
without userinfo, query strings, or fragments.
