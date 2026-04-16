# Checkpoint Design — agentkit Framework Layer

> **Status**: Archived — historical design draft
> **Date**: 2026-04-13
> **Scope**: `agentkit/checkpoint/`, `agentkit/storage/protocols.py` additions only.
> Product-layer integration (coding-agent) covered in Section 2 (pending).
>
> Decisions extracted to [ADR-0001](../adr/0001-checkpoint-captures-serialized-tape-and-plugin-state.md).
> Code is the source of truth. This document is retained as historical design context and is no longer maintained.

---

## Context & Constraints

### Architectural position

agentkit is a **standalone, reusable agent framework**. Checkpoint must be a
generic primitive that any agentkit consumer can adopt — coding-agent is one
consumer, not the only one.

### Design decisions (confirmed during brainstorm)

> Historical design context only: the accepted current `coding_agent` restore contract is defined by ADR-0003, ADR-0005, and ADR-0006. This document explains the framework primitives and option space that led to those decisions.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Checkpoint content | Tape + plugin states | External env snapshots are product-layer concerns |
| Restore semantics | Both restore + fork provided as primitives | Product layer picks per use case |
| Trigger model | Fully event-driven, zero automatic triggers in framework | Framework is unopinionated; product layer owns policy |
| State source | `PipelineContext` (ctx.tape, ctx.plugin_states) | Not PluginRegistry — avoids coupling to plugin instances |
| Restore mechanism | Reconstruction (new Tape + new ctx) | No in-place Tape mutation; preserves append/fork invariants |
| Plugin protocol | **Not extended** | StatefulPlugin opt-in lives in product layer, not agentkit |
| Checkpoint directive | Retained but **not activated** | Available for future manual savepoint use |

---

## New Modules

```
agentkit/
  checkpoint/
    __init__.py          # re-exports CheckpointService, models
    service.py           # CheckpointService
    models.py            # CheckpointMeta, CheckpointSnapshot
    serialize.py         # plugin_states serialization filter
  storage/
    protocols.py         # + CheckpointStore protocol
    checkpoint_fs.py     # file-based default implementation
```

---

## Data Models

```python
# agentkit/checkpoint/models.py

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CheckpointMeta:
    """Lightweight index record — safe to list in bulk.

    Stored separately from the full snapshot so that listing
    checkpoints never requires loading tape entries.
    """

    checkpoint_id: str
    tape_id: str
    session_id: str | None          # first-class; avoids digging into extra later
    entry_count: int
    window_start: int
    created_at: datetime
    label: str | None = None


@dataclass(frozen=True)
class CheckpointSnapshot:
    """Full checkpoint payload — loaded only on restore/reconstruct.

    tape_entries are serialized dicts (Entry.to_dict() products),
    NOT Entry instances. Checkpoint is a serialization artifact;
    the storage layer writes JSON directly without round-tripping
    through domain objects.
    """

    meta: CheckpointMeta
    tape_entries: tuple[dict[str, Any], ...]   # Entry.to_dict() products
    plugin_states: dict[str, Any]              # filtered serializable subset
    extra: dict[str, Any] = field(default_factory=dict)  # caller-provided, JSON-safe payload
```

### Type decisions

| Field | Type | Why |
|-------|------|-----|
| `tape_entries` | `tuple[dict, ...]` | Checkpoint is serialized form. `Tape.snapshot()` returns `Entry` objects; `capture()` converts via `entry.to_dict()`. Storage writes JSON directly. |
| `plugin_states` | `dict[str, Any]` | `ctx.plugin_states` values are not guaranteed to be `dict[str, Any]` — some plugins store flat values. Using `dict[str, Any]` matches the real type. |
| `session_id` | on `CheckpointMeta` | Product layer will need `list_by_session()` soon. Embedding `session_id` in meta avoids fishing it out of `extra` later. |

---

## Storage Protocol

```python
# agentkit/storage/protocols.py  (addition)

@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol for checkpoint persistence.

    Follows the same four-method shape as TapeStore and SessionStore.
    """

    async def save(self, snapshot: CheckpointSnapshot) -> None: ...
    async def load(self, checkpoint_id: str) -> CheckpointSnapshot | None: ...
    async def list_by_tape(self, tape_id: str) -> list[CheckpointMeta]: ...
    async def delete(self, checkpoint_id: str) -> None: ...
```

### File-based default implementation

Flat layout, indexed by `checkpoint_id`:

```
data/checkpoints/
  {checkpoint_id}.meta.json       # CheckpointMeta only
  {checkpoint_id}.entries.jsonl   # one Entry dict per line
  {checkpoint_id}.state.json      # { "plugin_states": {...}, "extra": {...} }
```

**Why flat, not nested under `{tape_id}/`**: `load(checkpoint_id)` and
`delete(checkpoint_id)` take only `checkpoint_id`. A nested layout forces
either a global index or a full directory scan to locate a checkpoint by id.
Flat layout gives O(1) access by id. `list_by_tape()` scans `.meta.json`
files and filters by `tape_id` field — acceptable for file-based store
volumes.

**Why three files, not one**:

- `.meta.json` — listing never loads entries
- `.entries.jsonl` — line-delimited entries allow streaming reads for large tapes
- `.state.json` — plugin states and extra in one JSON doc

**Write ordering**: files are written in dependency order —
`.entries.jsonl` first, then `.state.json`, then `.meta.json` last
(the commit marker). On delete, `.meta.json` is removed first.
This ensures `list_by_tape()` never surfaces a checkpoint whose
payload files are missing or partially written.

---

## Serialization Filter

```python
# agentkit/checkpoint/serialize.py

from __future__ import annotations

import json
from typing import Any


def extract_serializable_states(
    plugin_states: dict[str, Any],
) -> dict[str, Any]:
    """Filter ctx.plugin_states down to a JSON-safe subset.

    Strategy: strict whitelist via json.dumps() WITHOUT default=str.
    Values that fail serialization are silently dropped — they are
    typically runtime objects (plugin instances, DB connections, pools)
    that have no meaningful serialized form.

    This is intentionally conservative. Product-layer code that needs
    to persist complex state should use the ``extra`` parameter on
    capture() and handle its own serialization.
    """
    result: dict[str, Any] = {}
    for key, value in plugin_states.items():
        try:
            # Strict: no default=str, no custom encoder.
            # If it doesn't round-trip cleanly, we don't want it.
            serialized = json.dumps(value, allow_nan=False)
            result[key] = json.loads(serialized)
        except (TypeError, ValueError, OverflowError):
            continue
    return result


def validate_json_safe(data: dict[str, Any], *, name: str) -> None:
    """Fail-fast check that a dict is fully JSON-serializable.

    Raises TypeError with a clear message identifying the offending
    field. Used for caller-provided data (``extra``) where silent
    dropping would mask bugs.
    """
    try:
        json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"{name} must be JSON-serializable, got error: {exc}"
        ) from exc
```

Both functions use `allow_nan=False` so that `NaN`/`Infinity` values —
which Python's `json` module accepts by default but are **not valid JSON
per RFC 8259** — are rejected at serialization time rather than producing
non-interoperable checkpoint files.

### Two strategies, two use cases

| Data source | Strategy | Rationale |
|-------------|----------|-----------|
| `ctx.plugin_states` | `extract_serializable_states()` — silent drop | Framework-owned, heterogeneous, may contain runtime objects. Caller cannot control what plugins put here. |
| `extra` | `validate_json_safe()` — fail-fast | Caller-provided, explicitly intended for checkpoint. Silent field loss would mask bugs. |

---

## Non-goals

Section 1 deliberately does **not** do the following:

- **No in-place tape mutation** — no `Tape.reset_to()` or equivalent rewind API
- **No plugin instance introspection** — checkpoint capture never touches `PluginRegistry`
  or live plugin objects
- **No automatic trigger policy** — framework exposes checkpoint primitives; product layer
  decides when to call them
- **No external environment snapshotting** — git refs, sandbox state, MCP process state,
  and similar product/runtime concerns belong to Section 2

---

## CheckpointService

```python
# agentkit/checkpoint/service.py

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.checkpoint.serialize import (
    extract_serializable_states,
    validate_json_safe,
)
from agentkit.storage.protocols import CheckpointStore
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class CheckpointService:
    """Stateless checkpoint operations.

    All state comes from the caller (PipelineContext) or the store.
    This service never touches PluginRegistry or plugin instances.
    """

    def __init__(self, store: CheckpointStore) -> None:
        self._store = store

    # ── capture ─────────────────────────────────────────────

    async def capture(
        self,
        ctx: PipelineContext,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        """Snapshot current pipeline state.

        Reads from ctx only:
          - ctx.tape          → serialized entries
          - ctx.plugin_states → filtered to serializable subset
          - ctx.session_id    → stored in meta

        ``extra`` is caller-provided and MUST be JSON-serializable.
        Raises TypeError immediately if it is not (fail-fast).
        """
        if extra is not None:
            validate_json_safe(extra, name="extra")
        else:
            extra = {}

        meta = CheckpointMeta(
            checkpoint_id=uuid4().hex,
            tape_id=ctx.tape.tape_id,
            session_id=getattr(ctx, "session_id", None),
            entry_count=len(ctx.tape),
            window_start=ctx.tape.window_start,
            created_at=datetime.now(UTC),
            label=label,
        )
        snapshot = CheckpointSnapshot(
            meta=meta,
            tape_entries=tuple(
                entry.to_dict() for entry in ctx.tape.snapshot()
            ),
            plugin_states=extract_serializable_states(ctx.plugin_states),
            extra=extra,
        )
        await self._store.save(snapshot)
        return meta

    # ── restore (raw) ───────────────────────────────────────

    async def restore(
        self,
        checkpoint_id: str,
    ) -> CheckpointSnapshot:
        """Load full snapshot. Caller decides how to use it.

        Returns the raw snapshot — no Tape construction, no state
        injection. Product layer is responsible for reconstruction.
        """
        snapshot = await self._store.load(checkpoint_id)
        if snapshot is None:
            raise KeyError(f"Checkpoint {checkpoint_id!r} not found")
        return snapshot

    # ── reconstruct (convenience) ───────────────────────────

    async def reconstruct_tape(
        self,
        checkpoint_id: str,
        tape_id: str | None = None,
    ) -> tuple[Tape, dict[str, Any], dict[str, Any]]:
        """Convenience: snapshot → fresh Tape + plugin_states + extra.

        This is NOT a fork — there is no parent tape being mutated.
        It builds a brand-new Tape instance from persisted entries,
        suitable for injecting into a new PipelineContext.

        If ``tape_id`` is provided, the reconstructed Tape uses that
        identity (needed for truncate-rollback restore where the
        session's stable tape_id must be preserved). Otherwise a fresh
        UUID is generated.
        """
        snapshot = await self.restore(checkpoint_id)
        tape = Tape(
            entries=[Entry.from_dict(e) for e in snapshot.tape_entries],
            tape_id=tape_id or snapshot.meta.tape_id,
            _window_start=snapshot.meta.window_start,
        )
        return tape, dict(snapshot.plugin_states), dict(snapshot.extra)

    # ── list / delete ───────────────────────────────────────

    async def list(self, tape_id: str) -> list[CheckpointMeta]:
        """List checkpoint metadata for a given tape."""
        return await self._store.list_by_tape(tape_id)

    async def delete(self, checkpoint_id: str) -> None:
        """Delete a single checkpoint."""
        await self._store.delete(checkpoint_id)
```

---

## Automatic Checkpoint Triggering

### Why NOT via `on_checkpoint` hook

The existing `on_checkpoint` hook uses `HookRuntime.notify()`, which
**swallows all exceptions**. This is by design — it's an observer
notification for lightweight bookkeeping (topic detection, metrics,
MCP health checks).

Durable checkpoint persistence must **not** have its errors silently
swallowed. A failed checkpoint that the caller believes succeeded is
worse than no checkpoint at all.

### Why NOT via Checkpoint directive

The `Checkpoint` directive executes during `_stage_render()` /
`on_turn_end`, which is a different lifecycle boundary than
`_stage_save_state()`. Routing automatic checkpoints through the
directive system would create two checkpoint paths with different
timing semantics.

### Recommended approach

Product layer calls `checkpoint_service.capture(ctx)` **explicitly**
at a save boundary of its choosing. This can be:

- After `_stage_save_state()` (via a pipeline wrapper or post-stage hook)
- Inside a product-layer plugin's hook implementation (with proper error handling)
- In response to a user command (`/checkpoint save`)

The framework provides the tool (`CheckpointService`). The product
layer owns the policy (when, how often, what label).

### Responsibility matrix

| Mechanism | Purpose | Error handling |
|-----------|---------|----------------|
| `on_checkpoint` hook | Plugin-internal bookkeeping (topic, metrics, MCP) | Exceptions swallowed |
| `CheckpointService.capture()` | Durable state snapshot | Exceptions propagated to caller |
| `Checkpoint` directive | Reserved for future manual savepoint | Not currently wired |

---

## Impact on Existing Code

| Component | Change |
|-----------|--------|
| `Tape` | **None** — no new methods, no `reset_to()` |
| `Tape.snapshot()` / `Tape.fork()` | None — used internally by CheckpointService |
| `PipelineContext` | None — `capture()` reads `ctx.tape` + `ctx.plugin_states` |
| `Plugin` protocol | **None** — no serialization methods added |
| `PluginRegistry` | **None** — CheckpointService does not access it |
| `on_checkpoint` hook | Semantics unchanged (pure observer notification) |
| `Checkpoint` directive | Retained, not activated |
| `ForkTapeStore` | None — orthogonal (single-turn transaction vs cross-turn snapshot) |
| `TapeStore` / `SessionStore` | None — new `CheckpointStore` is a peer protocol |
| `storage/protocols.py` | **Added** `CheckpointStore` protocol |

---

## Open Items for Section 2

These are explicitly deferred to the coding-agent product-layer design:

1. **HTTP session tape continuity** — must be solved before checkpoint
   is useful; current `run_agent()` creates a fresh `Tape()` each turn
2. **Automatic checkpoint policy** — when to call `capture()`, label strategy
3. **Slash commands** — `/checkpoint save`, `/checkpoint list`, `/checkpoint restore`
4. **HTTP API endpoints** for checkpoint management
5. **`list_by_session(session_id)`** — likely needed; final ownership
   (framework store vs product-layer index) decided in Section 2
6. **External environment snapshots** (git ref, sandbox state) — via `extra` parameter
7. **Plugin state restore opt-in** — product-layer interface for plugins that need it
8. **Restore-in-place vs fork policy** — local (restore) vs multi-pod (fork)

For `coding_agent`, that policy has since been narrowed: restore is the same-session, same-stable-timeline controlled rollback path, while alternate exploration belongs on an explicit fork-style extension path rather than on `restore(...)` itself.
