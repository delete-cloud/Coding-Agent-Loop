---
id: PM-0027
title: Reconcile runtime projections on checkpoint restore
status: active
severity: high
confidence: high
subsystems:
  - checkpoint
  - runtime
  - ui
related_commits: []
related_files:
  - src/coding_agent/stores/durable_local.py
  - src/coding_agent/stores/durable_pg.py
  - src/coding_agent/stores/runtime_store.py
  - src/coding_agent/runs/query.py
  - src/coding_agent/server/http_server.py
  - src/coding_agent/acp/server.py
  - webui/app/src/App.tsx
  - webui/app/src/lib/api.ts
release_checks:
  - Verify SQLite and PostgreSQL restore reconcile the active run timeline atomically.
  - Verify session history, resume, result fallback, ACP, and WebUI use active runs only.
  - Verify direct run and event lookup still exposes superseded records for audit.
  - Verify a run created after restore remains active.
  - Verify executor claim paths reject superseded requested and expired runs.
  - Verify the reconciled current turn survives session reload.
---

# Summary

Restoring tape entries without reconciling durable runtime projections can make
post-checkpoint assistant replies reappear after a successful restore. The tape
is the conversation source of truth, but run records and display events remain
valid audit evidence and must not be deleted.

Persist an explicit supersession marker during the restore transaction. Product
timeline consumers must query active runs, while audit and control consumers keep
the full run set. Do not infer the active timeline only in a frontend, and do not
apply a permanent timestamp filter that would hide new runs after restore.

# Release Review Checklist

- Run focused SQLite and PostgreSQL durable restore tests.
- Run runtime-query, HTTP result/session-history, ACP load, and WebUI restore tests.
- Confirm superseded run events remain addressable by direct run id.
- Confirm post-restore runs are not hidden.
- Confirm superseded queued runs cannot be claimed and executed.
- Confirm restored session persistence reloads the latest active turn id.
