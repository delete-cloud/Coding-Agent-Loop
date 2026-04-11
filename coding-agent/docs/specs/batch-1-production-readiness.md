# Batch 1 Spec: Multi-Pod Production Readiness

> Status: DRAFT
> Author: Amp + review from Opus 4.6 / GPT 5.4
> Scope: agentkit core + coding-agent application + Helm chart

## Goal

Enable `replicaCount: 2+` deployment where multiple Pods share state safely,
with proper secrets management, health checks, and graceful shutdown.

---

## 1. Lifecycle Protocol (agentkit layer)

### File: `agentkit/runtime/lifecycle.py` (NEW)

Minimal lifecycle abstraction that coding-agent (and any future agentkit consumer)
can implement. This is the foundation for health checks and graceful shutdown.

```python
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class Lifecycle(Protocol):
    """Lifecycle hooks for agentkit runtimes."""

    async def on_startup(self) -> None:
        """Called once when the process starts.

        Responsibilities:
        - Initialize DB connection pools
        - Verify external service connectivity
        - Register with service discovery (if any)
        """
        ...

    async def on_shutdown(self, timeout: float = 30.0) -> None:
        """Called on SIGTERM / process exit.

        Responsibilities:
        - Stop accepting new turns
        - Wait for current turn's LLM round to finish (up to timeout)
        - Close DB connection pools
        - Flush pending metrics/logs

        Args:
            timeout: Max seconds to wait for in-flight work to drain.
        """
        ...

    async def health_check(self) -> dict[str, Any]:
        """Deep health check for /healthz.

        Returns:
            {"status": "ok"} or {"status": "degraded", "details": {...}}
            Must check: DB connection, Redis connection, worker readiness.
        """
        ...

    async def readiness_check(self) -> bool:
        """Shallow readiness check for /readyz.

        Returns:
            True if this instance can accept new work.
            False during startup warmup or shutdown drain.
        """
        ...
```

### Export

Add to `agentkit/__init__.py` exports: `Lifecycle`.

---

## 2. PG Storage Backend (agentkit layer)

### File: `agentkit/storage/pg.py` (NEW)

Implements `TapeStore` and `SessionStore` protocols using `asyncpg`.

#### Schema

```sql
-- Applied by Alembic (Batch 4) or manually for now
CREATE TABLE IF NOT EXISTS tape_entries (
    tape_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    entry      JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tape_id, seq)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for listing sessions ordered by activity
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions (updated_at DESC);
```

No `version` column — Tape is append-only, and `pg_advisory_lock` prevents
concurrent writes to the same session. Optimistic locking is for mutable
entities, not event logs.

#### Interface

```python
import asyncpg

class PGTapeStore:
    """TapeStore Protocol implementation backed by PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        """Append entries to tape_entries table.

        Uses a single transaction. seq is computed as
        max(existing seq for tape_id) + 1 + offset.
        """

    async def load(self, tape_id: str) -> list[dict[str, Any]]:
        """Load all entries for a tape, ordered by seq ASC."""

    async def list_ids(self) -> list[str]:
        """Return distinct tape_ids."""


class PGSessionStore:
    """SessionStore Protocol implementation backed by PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_session(self, session_id: str, data: dict[str, Any]) -> None:
        """UPSERT session data. Updates updated_at on conflict."""

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        """Load session by ID. Returns None if not found."""

    async def list_sessions(self) -> list[str]:
        """List all session IDs, ordered by updated_at DESC."""

    async def delete_session(self, session_id: str) -> None:
        """Delete a session by ID."""
```

#### Connection Pool Management

```python
class PGPool:
    """Manages asyncpg connection pool lifecycle.

    Created during on_startup(), closed during on_shutdown().
    """

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def open(self) -> None:
        """Create the connection pool. Called by Lifecycle.on_startup()."""
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
        )

    async def close(self) -> None:
        """Close the connection pool. Called by Lifecycle.on_shutdown()."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        """Get the pool. Raises if not opened."""
        if self._pool is None:
            raise RuntimeError("PGPool not initialized — call open() first")
        return self._pool

    async def ping(self) -> bool:
        """Health check: execute SELECT 1."""
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False
```

---

## 3. Session Lock (agentkit layer)

### File: `agentkit/storage/pg.py` (same file, additional class)

```python
class PGSessionLock:
    """Session-level advisory lock using PostgreSQL.

    Uses pg_advisory_lock (session-level, NOT transaction-level) because
    a single turn spans multiple DB transactions (tool calls, save_state).

    Lock is acquired at turn start, released at turn end.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._conn: asyncpg.Connection | None = None

    async def acquire(self, session_id: str) -> None:
        """Acquire advisory lock for a session.

        Uses hashtext(session_id) to generate a stable int8 lock key.
        Blocks until lock is available.
        """
        self._conn = await self._pool.acquire()
        await self._conn.execute(
            "SELECT pg_advisory_lock(hashtext($1))", session_id
        )

    async def release(self) -> None:
        """Release the advisory lock and return connection to pool."""
        if self._conn is not None:
            try:
                await self._conn.execute("SELECT pg_advisory_unlock_all()")
            finally:
                await self._pool.release(self._conn)
                self._conn = None
```

### Integration Point

The lock is acquired/released in `StoragePlugin` or `Pipeline`, NOT in
`PGTapeStore` itself. The lock scope is one full turn (run_turn), which
includes multiple DB operations.

```python
# In StoragePlugin.provide_storage() or a new hook:
# - acquire lock before run_turn
# - release lock after run_turn (in finally block)
```

---

## 4. StoragePlugin Backend Factory (coding-agent layer)

### File: `coding_agent/plugins/storage.py` (MODIFY)

Current `StoragePlugin.__init__` hardcodes `JSONLTapeStore`. Change to:

```python
class StoragePlugin:
    state_key = "storage"

    def __init__(
        self,
        data_dir: Path | None = None,
        backend: str = "jsonl",       # NEW: "jsonl" | "pg"
        pg_pool: PGPool | None = None,  # NEW: injected when backend="pg"
    ) -> None:
        self._data_dir = data_dir or Path(
            os.environ.get("AGENT_DATA_DIR", "./data")
        )
        self._backend = backend
        self._pg_pool = pg_pool
        # ... existing fields ...

    def _create_tape_store(self) -> TapeStore:
        if self._backend == "pg":
            if self._pg_pool is None:
                raise RuntimeError("PG backend requires pg_pool")
            from agentkit.storage.pg import PGTapeStore
            return PGTapeStore(self._pg_pool.pool)
        return JSONLTapeStore(self._data_dir / "tapes")

    def _create_session_store(self) -> SessionStore:
        if self._backend == "pg":
            if self._pg_pool is None:
                raise RuntimeError("PG backend requires pg_pool")
            from agentkit.storage.pg import PGSessionStore
            return PGSessionStore(self._pg_pool.pool)
        return FileSessionStore(self._data_dir / "sessions")
```

### Configuration

`agent.toml` already has `[storage]` section. Extend:

```toml
[storage]
backend = "pg"                              # "jsonl" | "pg"
dsn = "${DATABASE_URL}"                     # env var interpolation
pool_min_size = 2
pool_max_size = 10
```

Env var `DATABASE_URL` is injected via Helm secretEnv (see §8).

When `backend = "jsonl"`, behavior is unchanged (current default).

---

## 5. Shared Redis (coding-agent layer)

### Current State

- `RedisSessionStore` in `ui/session_store.py` — ✅ already works
- Redis sidecar in Helm — ❌ per-Pod, not shared
- Rate limiter — ❌ `storage_uri="memory://"`

### Changes

#### 5a. Rate Limiter → Redis backend

File: `coding_agent/ui/rate_limit.py` (MODIFY)

```python
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

def _get_storage_uri() -> str:
    redis_url = os.environ.get("AGENT_SESSION_REDIS_URL")
    if redis_url:
        return redis_url
    return "memory://"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=_get_storage_uri(),
)
```

#### 5b. Helm Redis: sidecar → external

In `values.yaml`, the `redis.url` field already exists. For production:

```yaml
redis:
  enabled: true
  url: "redis://redis-master.infra:6379/0"  # point to shared Redis
  sidecar:
    enabled: false   # disable per-Pod sidecar
```

No code change needed — `create_session_store()` already reads
`AGENT_SESSION_REDIS_URL` and the Helm deployment template already sets it
from `redis.url`.

---

## 6. Health Check Endpoints (coding-agent layer)

### File: `coding_agent/ui/http_server.py` (MODIFY)

Add deep health and readiness endpoints:

```python
@app.get("/healthz")
async def healthz():
    """Deep health check — verifies all dependencies."""
    checks = {}

    # Check PG
    if pg_pool is not None:
        checks["postgres"] = "ok" if await pg_pool.ping() else "fail"

    # Check Redis
    redis_url = os.environ.get("AGENT_SESSION_REDIS_URL")
    if redis_url:
        try:
            store = app.state.session_manager._store
            if hasattr(store, "_client"):
                store._client.ping()
                checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "fail"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503
    return JSONResponse(
        {"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=status_code,
    )

@app.get("/readyz")
async def readyz():
    """Readiness check — can this instance accept work?"""
    if app.state.get("draining", False):
        return JSONResponse({"ready": False}, status_code=503)
    return {"ready": True}
```

### Helm Probe Update

Current probes point to `/health`. Change to:

```yaml
readinessProbe:
  httpGet:
    path: /readyz    # was /health
    port: http
livenessProbe:
  httpGet:
    path: /healthz   # was /health
    port: http
```

---

## 7. Graceful Shutdown (coding-agent layer)

### File: `coding_agent/ui/http_server.py` (MODIFY)

```python
import asyncio
import signal

_shutdown_event = asyncio.Event()

@app.on_event("startup")
async def startup():
    # Initialize PG pool if configured
    if pg_pool is not None:
        await pg_pool.open()

    # Register SIGTERM handler
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, _initiate_shutdown)

@app.on_event("shutdown")
async def shutdown():
    # Close PG pool
    if pg_pool is not None:
        await pg_pool.close()

def _initiate_shutdown():
    """SIGTERM handler: mark as draining, let current work finish."""
    app.state.draining = True
    # readyz will return 503, K8S stops sending traffic
    # Current in-flight turns continue until completion
    _shutdown_event.set()
```

### Helm

```yaml
# values.yaml
terminationGracePeriodSeconds: 600
```

Behavior on SIGTERM:
1. `_initiate_shutdown()` sets `draining = True`
2. `/readyz` returns 503 → K8S stops routing new requests
3. In-flight `run_turn` continues (current LLM round only)
4. After turn completes naturally, uvicorn shuts down
5. K8S waits up to 600s before SIGKILL

---

## 8. Helm: Secrets Management

### File: `helm/templates/deployment.yaml` (MODIFY)

Add `secretRef` and `secretEnv` support:

```yaml
# In container env section, after existing env vars:
{{- if .Values.agent.secretRef }}
envFrom:
  - secretRef:
      name: {{ .Values.agent.secretRef }}
{{- end }}
{{- range .Values.agent.secretEnv }}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secretKeyRef.name }}
      key: {{ .secretKeyRef.key }}
{{- end }}
```

### File: `helm/values.yaml` (MODIFY)

```yaml
agent:
  secretRef: ""          # Bulk-inject all keys from a K8S Secret
  secretEnv: []          # Selective secret injection
  # Example:
  # secretEnv:
  #   - name: DATABASE_URL
  #     secretKeyRef:
  #       name: coding-agent-secrets
  #       key: database-url
  #   - name: AGENT_API_KEY
  #     secretKeyRef:
  #       name: coding-agent-secrets
  #       key: api-key
  #   - name: AGENT_SESSION_REDIS_URL
  #     secretKeyRef:
  #       name: coding-agent-secrets
  #       key: redis-url
```

---

## 9. Helm: HPA + PDB

### File: `helm/templates/hpa.yaml` (NEW)

```yaml
{{- if .Values.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "coding-agent.fullname" . }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "coding-agent.fullname" . }}
  minReplicas: {{ .Values.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetCPUUtilizationPercentage }}
{{- end }}
```

### File: `helm/templates/pdb.yaml` (NEW)

```yaml
{{- if .Values.podDisruptionBudget.enabled }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "coding-agent.fullname" . }}
spec:
  minAvailable: {{ .Values.podDisruptionBudget.minAvailable }}
  selector:
    matchLabels:
      {{- include "coding-agent.selectorLabels" . | nindent 6 }}
{{- end }}
```

### File: `helm/values.yaml` (MODIFY — append)

```yaml
autoscaling:
  enabled: false
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

podDisruptionBudget:
  enabled: false
  minAvailable: 1

terminationGracePeriodSeconds: 600
```

---

## 10. Dependency Changes

### File: `pyproject.toml` (MODIFY)

```toml
[project.optional-dependencies]
pg = [
    "asyncpg>=0.29.0",
]
```

`asyncpg` is an optional dependency — only needed when `backend = "pg"`.
The Docker image installs it explicitly:

```dockerfile
RUN uv pip install --python /app/.venv/bin/python redis asyncpg
```

---

## File Change Summary

| File | Action | Layer |
|------|--------|-------|
| `agentkit/runtime/lifecycle.py` | NEW | agentkit |
| `agentkit/storage/pg.py` | NEW | agentkit |
| `agentkit/__init__.py` | MODIFY (add Lifecycle export) | agentkit |
| `coding_agent/plugins/storage.py` | MODIFY (backend factory) | coding-agent |
| `coding_agent/ui/rate_limit.py` | MODIFY (Redis storage_uri) | coding-agent |
| `coding_agent/ui/http_server.py` | MODIFY (healthz/readyz + shutdown) | coding-agent |
| `coding_agent/agent.toml` | MODIFY (storage.dsn) | coding-agent |
| `pyproject.toml` | MODIFY (asyncpg optional dep) | coding-agent |
| `Dockerfile` | MODIFY (install asyncpg) | coding-agent |
| `helm/values.yaml` | MODIFY (secretEnv, HPA, PDB, gracePeriod) | helm |
| `helm/templates/deployment.yaml` | MODIFY (secretRef, probe paths, gracePeriod) | helm |
| `helm/templates/hpa.yaml` | NEW | helm |
| `helm/templates/pdb.yaml` | NEW | helm |

**Total: 5 new files, 8 modified files**

---

## What This Does NOT Include (deferred)

- Schema migration tooling (Alembic) → Batch 4
- OTel / distributed tracing → Batch 3
- Kafka / EventBus → Batch 4
- Session unification → Not planned (different concerns)
- MySQL support → Not planned (PG covers all needs)
- PGVector DocIndex → Batch 4

---

## Verification Criteria

1. `replicaCount: 2` deploys successfully with shared PG + Redis
2. Two Pods cannot run turns for the same session concurrently (advisory lock blocks)
3. Pod restart does not lose tape data (PG persisted)
4. `/healthz` returns degraded when PG is down
5. `/readyz` returns 503 after SIGTERM, in-flight turn completes
6. Rate limiting is enforced across Pods (Redis-backed)
7. No secrets in plain text in Helm values (all via secretRef/secretEnv)
8. HPA scales up when CPU > 70%
9. PDB prevents all Pods being evicted simultaneously
10. `backend = "jsonl"` still works unchanged (backward compat)
