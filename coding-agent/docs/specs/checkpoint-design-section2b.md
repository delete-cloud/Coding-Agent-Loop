# Section 2B — Checkpoint Integration (Product Layer)

> **Status**: Draft
> **Date**: 2026-04-13
> **Depends on**: Section 1 (agentkit checkpoint primitives), Section 2A (session tape continuity)
> **Scope**: `coding_agent/` primarily. One agentkit change: `TapeStore.truncate()` protocol addition.

---

## Problem Statement

Section 2A gives us session-stable tape persistence and hot/cold lifecycle.
Section 1 gives us `CheckpointService` with `capture()` / `restore()` /
`reconstruct_tape()` primitives.

Section 2B wires them together: user-facing checkpoint commands, restore
flow (truncate rollback), and the one agentkit-level prerequisite that
Section 1 did not address — `TapeStore.truncate()`.

---

## Prerequisite: TapeStore.truncate() (agentkit change)

### Why

Checkpoint restore uses truncate rollback: load the full tape, keep only
the first `entry_count` entries, discard the rest. The in-memory Tape is
easy — just slice. But the persisted tape in TapeStore must also be
truncated, otherwise the next `ForkTapeStore.commit()` would append after
the old (longer) tail, creating a corrupted timeline.

### Protocol addition

```python
# agentkit/storage/protocols.py

@runtime_checkable
class TapeStore(Protocol):
    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None: ...
    async def load(self, tape_id: str) -> list[dict[str, Any]]: ...
    async def list_ids(self) -> list[str]: ...
    async def truncate(self, tape_id: str, keep: int) -> None: ...   # NEW
```

### JSONL implementation

```python
# coding_agent/plugins/storage.py — JSONLTapeStore

async def truncate(self, tape_id: str, keep: int) -> None:
    """Keep only the first `keep` entries, discard the rest."""
    path = self._path_for(tape_id)
    if not path.exists():
        return

    def _truncate() -> None:
        lines: list[str] = []
        with open(path) as f:
            for i, line in enumerate(f):
                if i >= keep:
                    break
                lines.append(line)
        with open(path, "w") as f:
            f.writelines(lines)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _truncate)
```

### PG implementation

```python
# agentkit/storage/pg.py — PGTapeStore

_TRUNCATE_SQL: Final[str] = (
    "DELETE FROM agent_tapes WHERE tape_id = $1 AND seq >= $2"
)

async def truncate(self, tape_id: str, keep: int) -> None:
    pool = await self._ensure_schema()
    await pool.execute(self._TRUNCATE_SQL, tape_id, keep)
```

### Impact

- `TapeStore` protocol gains one method
- Both implementations get ~10 lines each
- No existing code calls `truncate()` — purely additive
- `ForkTapeStore` does NOT need a truncate method; it delegates to
  `self._backing` which is a `TapeStore`

---

## Checkpoint ↔ Tape Relationship

Conceptually, a checkpoint behaves like a **bookmark on the session tape** —
it records a position to return to, not a separate copy of the conversation.
In v1 implementation, the snapshot still stores a full tape copy for
self-contained restore safety.

```
CheckpointSnapshot
  ├─ meta.tape_id        → points to the session's stable tape id
  ├─ meta.entry_count    → "restore to this length"
  ├─ meta.window_start   → tape window position at capture time
  ├─ plugin_states       → serializable subset of ctx.plugin_states
  └─ extra               → product-layer payload (git ref, workspace, etc.)
```

Section 1's `CheckpointSnapshot` also stores `tape_entries` (a full copy).
For the bookmark model, we have two options:

| Approach | Stores entries? | Restore reads from | Trade-off |
|----------|----------------|-------------------|-----------|
| A: Bookmark only | No | TapeStore | Smaller snapshots; depends on tape integrity |
| B: Full copy (Section 1 current) | Yes | Snapshot itself | Self-contained; redundant storage |

**Decision: keep Section 1's full-copy model unchanged.** Reasons:

1. Section 1 is already finalized with `tape_entries` in the snapshot
2. Full copy makes checkpoint self-contained — survives tape truncation/corruption
3. `reconstruct_tape()` already builds from `snapshot.tape_entries`
4. Storage cost is marginal for the volumes we expect

The bookmark mental model still holds for the user: "save a point, restore
to that point." Implementation happens to store a full copy for safety.

### Restore = truncate rollback

When restoring checkpoint C at entry_count=N:

```
Before restore:
  TapeStore: [e0, e1, ..., e(N-1), eN, ..., eM]    (M entries total)
  Checkpoint C: entry_count=N

After restore:
  TapeStore: [e0, e1, ..., e(N-1)]                  (truncated to N)
  New Tape:  built from checkpoint's tape_entries     (N entries)
  Session:   cold path rebuild with new Tape
```

Post-restore turns append starting from position N, as if the truncated
entries never happened. **No fork, no new tape_id.** The session's stable
tape_id is preserved.

---

## Restore Flow

### Step by step

```python
async def _restore_checkpoint(
    self, session: Session, checkpoint_id: str
) -> None:
    """Restore session to a checkpoint. Truncate-rollback semantics."""

    # 1. Load checkpoint
    snapshot = await self._checkpoint_service.restore(checkpoint_id)
    meta = snapshot.meta

    # 2. Validate: checkpoint belongs to this session's tape
    if meta.tape_id != session.tape_id:
        raise ValueError(
            f"Checkpoint {checkpoint_id} belongs to tape {meta.tape_id}, "
            f"not session tape {session.tape_id}"
        )

    # 3. Truncate persisted tape to checkpoint's entry_count
    await self._tape_store.truncate(session.tape_id, meta.entry_count)

    # 4. Rebuild tape from checkpoint's stored entries
    tape = Tape(
        entries=[Entry.from_dict(e) for e in snapshot.tape_entries],
        tape_id=session.tape_id,          # preserve stable id
        _window_start=meta.window_start,
    )

    # 5. Tear down current hot session (if any)
    self._evict_runtime(session)

    # 6. Rebuild pipeline via cold path
    pipeline, ctx = create_agent(
        workspace_root=session.repo_path,
        model_override=session.model_name,
        provider_override=session.provider_name,
        base_url_override=session.base_url,
        max_steps_override=session.max_steps,
        approval_mode_override=_approval_mode_str(session.approval_policy),
        session_id_override=session.id,
        tape=tape,
    )

    # 7. Optionally inject checkpoint plugin_states into ctx
    #    (lightweight hint only — not a general rehydration mechanism)
    #    setdefault: plugins that set their own state during mount won't be
    #    overwritten; only plugins that don't touch ctx.plugin_states get
    #    the checkpoint's version. Most critical runtime state (shell cwd,
    #    MCP connections, memory working set) lives outside ctx.plugin_states
    #    and is NOT restored here — this is degraded continuity, same as 2A.
    for key, value in snapshot.plugin_states.items():
        ctx.plugin_states.setdefault(key, value)

    # 8. Wire up consumer + adapter
    consumer = self._make_consumer(session)
    ctx.config["wire_consumer"] = consumer
    ctx.config["agent_id"] = ""
    adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)

    # 9. Update session
    session.pipeline = pipeline
    session.pipeline_ctx = ctx
    session.consumer = consumer
    session.adapter = adapter
    # tape_id unchanged — same stable id
    self._persist_session(session)
```

### Key design points

1. **Tape truncation before rebuild** — ensures next turn's
   `ForkTapeStore.commit()` appends at the correct position
2. **`tape_id` preserved** — no fork, no new id. Session continues
   on the same stable tape
3. **Plugin states are best-effort hint only** — `plugin_states` injection
   is a lightweight hint channel for plugins that already read from
   `ctx.plugin_states`. It is not a general plugin rehydration mechanism.
   Most critical runtime state (shell cwd/env, MCP connection state, memory
   working set, metrics accumulators) lives outside `ctx.plugin_states` and
   is not restored here. This is degraded continuity, same as 2A's cold path.
4. **Hot session is torn down first** — `_evict_runtime()` clears
   pipeline/ctx/adapter/consumer, then rebuild happens cleanly

### `_evict_runtime` helper

```python
def _evict_runtime(self, session: Session) -> None:
    """Clear runtime objects from session (for eviction or restore)."""
    session.pipeline = None
    session.pipeline_ctx = None
    session.adapter = None
    session.consumer = None
```

Used by both LRU eviction (2A) and checkpoint restore (2B).

---

## Slash Commands

### `/checkpoint save [label]`

```python
async def _handle_checkpoint_save(
    self, session: Session, label: str | None = None
) -> str:
    if session.pipeline_ctx is None:
        return "No active session to checkpoint."

    # Default label from current topic if available
    if label is None:
        label = self._default_checkpoint_label(session.pipeline_ctx)

    meta = await self._checkpoint_service.capture(
        ctx=session.pipeline_ctx,
        label=label,
        extra=self._collect_extra(session),
    )

    return f"Checkpoint saved: {meta.label or meta.checkpoint_id[:8]} (turn {meta.entry_count})"
```

#### Default label from topic

```python
def _default_checkpoint_label(self, ctx: PipelineContext) -> str:
    """Use current topic's label as default checkpoint label."""
    topic_state = ctx.plugin_states.get("topic", {})
    topic_id = topic_state.get("current_topic_id")
    if not topic_id:
        return ""
    for entry in reversed(list(ctx.tape)):
        if (
            entry.kind == "anchor"
            and entry.meta.get("topic_id") == topic_id
            and entry.meta.get("prefix") == "Topic Start"
        ):
            return entry.payload.get("content", "")
    return ""
```

#### `_collect_extra` — product-layer snapshot payload

```python
def _collect_extra(self, session: Session) -> dict[str, Any]:
    """Collect product-specific metadata for checkpoint extra field."""
    extra: dict[str, Any] = {}

    # Git ref (if workspace is a git repo)
    if session.repo_path is not None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=session.repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                extra["git_ref"] = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    extra["workspace"] = str(session.repo_path) if session.repo_path else None
    extra["model"] = session.model_name
    extra["provider"] = session.provider_name
    return extra
```

### `/checkpoint list`

```python
async def _handle_checkpoint_list(self, session: Session) -> str:
    if session.tape_id is None:
        return "No checkpoints — session has no tape history."

    metas = await self._checkpoint_service.list(session.tape_id)
    if not metas:
        return "No checkpoints saved."

    # Sort by entry_count (chronological)
    metas.sort(key=lambda m: m.entry_count)

    lines = []
    for i, meta in enumerate(metas, 1):
        label = meta.label or meta.checkpoint_id[:8]
        lines.append(f"#{i}  [turn {meta.entry_count}]  {label}")

    return "\n".join(lines)
```

Output example:

```
#1  [turn 12]  重构 SessionManager 的 cold restore 逻辑
#2  [turn 28]  修复 ForkTapeStore identity bug
#3  [turn 35]  添加 LRU eviction 测试
```

### `/checkpoint restore <id>`

```python
async def _handle_checkpoint_restore(
    self, session: Session, checkpoint_ref: str
) -> str:
    """Restore to a checkpoint by # index or checkpoint_id prefix."""

    # Resolve reference
    checkpoint_id = await self._resolve_checkpoint_ref(
        session, checkpoint_ref
    )
    if checkpoint_id is None:
        return f"Checkpoint '{checkpoint_ref}' not found."

    if session.turn_in_progress:
        return "Cannot restore while a turn is in progress."

    await self._restore_checkpoint(session, checkpoint_id)
    return f"Restored to checkpoint. Next turn continues from turn {session.pipeline_ctx and len(session.pipeline_ctx.tape)}."


async def _resolve_checkpoint_ref(
    self, session: Session, ref: str
) -> str | None:
    """Resolve '#2' or 'abc123' to a checkpoint_id."""
    if session.tape_id is None:
        return None

    metas = await self._checkpoint_service.list(session.tape_id)
    metas.sort(key=lambda m: m.entry_count)

    # Try as #index (1-based)
    if ref.startswith("#") and ref[1:].isdigit():
        idx = int(ref[1:]) - 1
        if 0 <= idx < len(metas):
            return metas[idx].checkpoint_id
        return None

    # Try as checkpoint_id prefix
    matches = [m for m in metas if m.checkpoint_id.startswith(ref)]
    if len(matches) == 1:
        return matches[0].checkpoint_id
    return None
```

### Slash command routing

```python
# In SessionManager or command router

async def handle_slash_command(
    self, session: Session, command: str, args: str
) -> str | None:
    match command:
        case "checkpoint":
            parts = args.strip().split(maxsplit=1)
            subcmd = parts[0] if parts else ""
            rest = parts[1] if len(parts) > 1 else ""

            match subcmd:
                case "save":
                    return await self._handle_checkpoint_save(
                        session, label=rest or None
                    )
                case "list":
                    return await self._handle_checkpoint_list(session)
                case "restore":
                    if not rest:
                        return "Usage: /checkpoint restore <#index or id>"
                    return await self._handle_checkpoint_restore(
                        session, rest
                    )
                case _:
                    return "Usage: /checkpoint <save [label] | list | restore <ref>>"
        case _:
            return None
```

### Command integration: two paths

Slash commands currently exist **only in the REPL** (`cli/commands.py`),
routed via a `_COMMANDS` dict registry with `@command` decorators. The
HTTP path (`http_server.py` → `session_manager.run_agent()`) passes all
prompts directly to the pipeline with no command interception.

Checkpoint commands must work in both paths.

#### REPL path

Register in `cli/commands.py` using the existing `@command` decorator.

**REPL context dependencies**: `CheckpointService` and `TapeStore` are
**not** currently in the REPL context dict. `ReplSession._setup_agent()`
populates context with `tool_registry`, `skills_plugin`, `pipeline_ctx`,
and `mcp_plugin` (see `cli/repl.py:117-124`). Before the REPL command
works, two things must be added to `_setup_agent()`:

```python
# coding_agent/cli/repl.py — _setup_agent() additions
data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
tape_store = JSONLTapeStore(data_dir / "tapes")
checkpoint_store = FSCheckpointStore(data_dir / "checkpoints")
checkpoint_service = CheckpointService(store=checkpoint_store)

self.context["tape_store"] = tape_store
self.context["checkpoint_service"] = checkpoint_service
```

This is a small but required change — not "add a command and it works."

```python
# coding_agent/cli/commands.py

@command("checkpoint", "Save, list, or restore checkpoints")
async def cmd_checkpoint(args: list[str], context: dict[str, Any]) -> None:
    subcmd = args[0] if args else ""
    rest = " ".join(args[1:])

    ctx: PipelineContext = context["pipeline_ctx"]
    checkpoint_svc: CheckpointService = context["checkpoint_service"]

    match subcmd:
        case "save":
            meta = await checkpoint_svc.capture(ctx, label=rest or None)
            print_html(f"Checkpoint saved: {meta.label or meta.checkpoint_id[:8]} (turn {meta.entry_count})")
        case "list":
            metas = await checkpoint_svc.list(ctx.tape.tape_id)
            # ... format and print (same logic as SessionManager version)
        case "restore":
            # REPL restore is more invasive: must replace ctx.tape in-place
            # and re-mount. Deferred to v2 — REPL restore requires pipeline
            # lifecycle changes that don't exist yet.
            print_html("<ansired>Checkpoint restore is not yet supported in REPL mode.</ansired>")
        case _:
            print_html("Usage: /checkpoint <save [label] | list | restore <ref>>")
```

**Why REPL restore is deferred**: The REPL's `ReplSession` owns the
pipeline and ctx directly — there's no hot/cold path, no SessionManager.
Restoring means replacing `ctx.tape` mid-session and re-running
`pipeline.mount()`, which is not idempotent for all plugins. Save and
list work fine; restore needs REPL lifecycle work that belongs in a
separate PR.

#### HTTP path

Add command interception in `session_manager.run_agent()`:

```python
async def run_agent(self, session_id: str, prompt: str) -> None:
    session = self.get_session(session_id)

    # Intercept slash commands before pipeline execution
    if prompt.startswith("/"):
        result = await self._handle_slash_command(session, prompt)
        if result is not None:
            # Emit result as a stream delta + TurnEnd on the wire.
            # SystemMessage does not exist in the wire protocol;
            # use StreamDelta with role="assistant" for command output.
            await session.wire.send(
                StreamDelta(
                    session_id=session_id,
                    agent_id="",
                    content=result,
                    role="assistant",
                )
            )
            await session.wire.send(
                TurnEnd(
                    session_id=session_id,
                    agent_id="",
                    turn_id=uuid.uuid4().hex,
                    completion_status=CompletionStatus.COMPLETED,
                )
            )
            return

    # ... existing turn logic (hot/cold path) ...
```

This keeps command dispatch inside SessionManager where it has access
to `CheckpointService`, `TapeStore`, and `Session` — no changes needed
in `http_server.py`.

#### Why not a shared command registry?

REPL uses `context: dict[str, Any]` with pipeline_ctx. HTTP uses
`Session` with SessionManager. The runtime shapes are different enough
that a shared registry would need an abstraction layer that isn't
justified for 3 commands. Keep them separate; converge if command count
grows.

---

## Automatic Checkpoint Policy (v1)

### Strategy: explicit only

v1 does **not** auto-checkpoint. Reasons:

1. Auto-checkpoint policy is product UX — needs user research on
   what granularity is useful
2. Every checkpoint stores a full tape copy — auto-checkpointing
   every N turns could generate significant storage
3. Manual `/checkpoint save` is sufficient for the initial use case
   (debug, rollback, exploration)

### Future candidates (not implemented)

If auto-checkpoint is needed later, natural trigger points:

| Trigger | When | Trade-off |
|---------|------|-----------|
| Every N turns | After every 10/20 turns | Simple but noisy |
| Before destructive tool calls | Before `bash rm`, `git reset`, etc. | Targeted but requires tool classification |
| On topic boundary | When TopicPlugin detects topic_end | Natural segmentation but topic detection is heuristic |
| On idle timeout | Session inactive > T minutes | Good for crash recovery but overlaps with tape persistence |

These are all product-layer decisions. The framework (`CheckpointService`)
is already capable — only the trigger wiring is needed.

---

## CheckpointService Wiring

### Where does CheckpointService live?

`SessionManager` owns it, same level as `_tape_store`:

```python
class SessionManager:
    def __init__(
        self,
        store: SessionStore | None = None,
        tape_store: TapeStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ):
        self._store = store or create_session_store()
        self._tape_store = tape_store or self._create_default_tape_store()
        self._checkpoint_service = CheckpointService(
            store=checkpoint_store or self._create_default_checkpoint_store()
        )
        ...

    def _create_default_checkpoint_store(self) -> CheckpointStore:
        """v1: file-based checkpoint store in same data_dir."""
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        return FSCheckpointStore(data_dir / "checkpoints")
```

### Same ownership pattern as TapeStore

| Component | Owner | Config source |
|-----------|-------|---------------|
| TapeStore (continuity) | SessionManager | storage backend config |
| TapeStore (runtime) | StoragePlugin | storage backend config |
| CheckpointStore | SessionManager | storage backend config |
| CheckpointService | SessionManager | wraps CheckpointStore |

v1: all file-based, same `data_dir`. Same constraint as 2A — shared
factory is a v2 concern when PG backend is needed.

---

## Data Flow: Checkpoint Save

```
/checkpoint save "before refactor"
  │
  ├─ session.pipeline_ctx exists? (must be hot)
  ├─ _default_checkpoint_label(ctx)  ← fallback to topic label
  ├─ _collect_extra(session)         ← git ref, workspace, model
  ├─ checkpoint_service.capture(ctx, label, extra)
  │   ├─ serialize tape entries      ← ctx.tape.snapshot() → to_dict()
  │   ├─ filter plugin_states        ← extract_serializable_states()
  │   ├─ validate extra              ← fail-fast if not JSON-safe
  │   └─ checkpoint_store.save(snapshot)
  │       ├─ {id}.meta.json
  │       ├─ {id}.entries.jsonl
  │       └─ {id}.state.json
  └─ "Checkpoint saved: before refactor (turn 28)"
```

## Data Flow: Checkpoint Restore

```
/checkpoint restore #2
  │
  ├─ _resolve_checkpoint_ref("#2")   ← list + index lookup
  ├─ turn_in_progress? → reject
  ├─ _restore_checkpoint(session, checkpoint_id)
  │   ├─ checkpoint_service.restore(id) → snapshot
  │   ├─ validate tape_id matches session
  │   ├─ tape_store.truncate(tape_id, entry_count)   ← NEW
  │   ├─ Tape(entries=snapshot.tape_entries, tape_id=stable_id)
  │   ├─ _evict_runtime(session)
  │   ├─ create_agent(tape=restored_tape, ...)
  │   ├─ plugin_states best-effort inject
  │   ├─ wire consumer + adapter
  │   └─ persist session metadata
  └─ "Restored to checkpoint. Next turn continues from turn 28."
```

---

## Checkpoint Invalidation on Restore

### The problem

When restoring to checkpoint #1 (entry_count=12), checkpoints #2
(entry_count=28) and #3 (entry_count=35) still exist in the
CheckpointStore. Their `entry_count` values point past the new tape end.
Post-restore turns will generate new entries at positions 12, 13, 14...
which diverge from what #2 and #3 captured.

```
Before restore:
  Tape: [e0 ... e11 | e12 ... e27 | e28 ... e34]
  CP#1: entry_count=12  ← restoring to here
  CP#2: entry_count=28  ← now stale
  CP#3: entry_count=35  ← now stale

After restore + 5 new turns:
  Tape: [e0 ... e11 | e12' e13' e14' e15' e16']
  CP#2: entry_count=28  ← points beyond tape end (17 entries)
  CP#3: entry_count=35  ← points beyond tape end
```

### v1 strategy: delete-ahead checkpoints (single-timeline policy)

This is a **v1 policy choice**, not an intrinsic requirement of the
checkpoint design. It assumes a single timeline: restore rolls back the
tape, and subsequent turns build forward from that point. If Section 3
introduces checkpoint branching or forked timelines, this policy would
be revisited.

Delete checkpoints whose `entry_count > restored_entry_count`:

```python
async def _restore_checkpoint(self, session: Session, checkpoint_id: str) -> None:
    snapshot = await self._checkpoint_service.restore(checkpoint_id)
    meta = snapshot.meta

    # ... validate tape_id ...

    # Invalidate checkpoints that are "ahead" of the restore point
    all_metas = await self._checkpoint_service.list(session.tape_id)
    for m in all_metas:
        if m.entry_count > meta.entry_count:
            await self._checkpoint_service.delete(m.checkpoint_id)

    # ... truncate, rebuild, etc. ...
```

**Why delete, not mark**: Stale checkpoints are actively dangerous —
restoring a stale checkpoint would truncate to an entry_count that
doesn't match the current tape timeline. Deletion is the safest option.
The full-copy model means no data is truly lost (the snapshot's
`tape_entries` contained the old timeline), but we don't keep them
because they'd confuse the user and create restore-to-wrong-timeline
bugs.

**Checkpoint being restored is preserved** — only checkpoints *after*
the restore point are deleted.

---

## Error Recovery

### Truncate succeeds, rebuild fails

This is the critical failure mode. If `tape_store.truncate()` succeeds
but `create_agent()` or `pipeline.mount()` fails, the tape is truncated
but no working pipeline exists.

**Mitigation**: The checkpoint's `tape_entries` are the source of truth.
The truncated tape can be reconstructed:

```python
async def _restore_checkpoint(self, session: Session, checkpoint_id: str) -> None:
    snapshot = await self._checkpoint_service.restore(checkpoint_id)
    meta = snapshot.meta

    # ... validate ...

    # Truncate persisted tape
    await self._tape_store.truncate(session.tape_id, meta.entry_count)

    try:
        # Rebuild tape and pipeline
        tape = Tape(
            entries=[Entry.from_dict(e) for e in snapshot.tape_entries],
            tape_id=session.tape_id,
            _window_start=meta.window_start,
        )
        self._evict_runtime(session)
        pipeline, ctx = create_agent(tape=tape, ...)
        # ... wire up ...
    except Exception:
        # Rebuild failed. Session is in a degraded state: tape truncated,
        # no live pipeline. But checkpoint snapshot is intact, and session
        # metadata still has tape_id. Next run_agent() call will trigger
        # cold restore from TapeStore (which has the truncated-but-consistent
        # entries). Log and propagate.
        logger.error(
            "Pipeline rebuild failed after truncate for session %s, "
            "checkpoint %s. Session will cold-restore on next turn.",
            session.id, checkpoint_id,
        )
        raise
```

**Key insight**: truncate + failed rebuild is NOT data loss. The tape is
shortened to a consistent state matching the checkpoint. A subsequent
`run_agent()` call will follow the normal cold-restore path from the
(now-truncated) tape. However, if the rebuild failure is caused by a
persistent issue (misconfigured provider, plugin mount error, etc.), the
next call will also fail. Logging and error propagation to the caller
is necessary; do not assume a retry will always succeed.

### Checkpoint store unavailable

If `FSCheckpointStore` can't read/write (disk full, permissions):
- `capture()` raises → user sees error, no checkpoint saved (safe)
- `restore()` raises → user sees error, no state changed (safe)
- `list()` raises → user sees error (safe)

No special handling needed. Errors propagate naturally.

---

## `list_by_session` Resolution

Section 1 flagged `list_by_session(session_id)` as an open item (#5).

### Decision: product-layer convenience, not a store method

`CheckpointStore.list_by_tape(tape_id)` is the framework primitive.
Since each session has exactly one stable `tape_id` (established by 2A),
`list_by_session` is trivially:

```python
async def list_checkpoints_for_session(self, session: Session) -> list[CheckpointMeta]:
    if session.tape_id is None:
        return []
    return await self._checkpoint_service.list(session.tape_id)
```

This lives in `SessionManager` (product layer), not in `CheckpointStore`
(framework). The mapping `session_id → tape_id` is a product-layer
concept. Framework store methods should not depend on session semantics.

If multi-tape sessions ever exist (unlikely given 2A's stable-id design),
the product layer would maintain a `session_id → [tape_id, ...]` index
and fan out queries. Still no change to the framework store.

---

## Relationship to Topic

Topic and checkpoint are **decoupled by design**.

Both may occur near turn boundaries, but serve different purposes:

| Concept | Purpose | Trigger |
|---------|---------|---------|
| Topic | Tape segmentation / structure boundary | Automatic (file overlap heuristic) |
| Checkpoint | Named restorable snapshot | Explicit (user command or future policy) |

Topic's only contribution to checkpoint: **default label fallback**.
When `/checkpoint save` is called without a label, the current topic's
label is used. This is a convenience, not a dependency.

Topic does not trigger checkpoints. Checkpoints do not affect topic
detection. They are orthogonal systems that happen to share the tape
as their underlying data structure.

---

## File-level Change Summary

| File | Change | Size |
|------|--------|------|
| `agentkit/storage/protocols.py` | `TapeStore` + `truncate()` method | 1 line |
| `coding_agent/plugins/storage.py` | `JSONLTapeStore.truncate()` | ~15 lines |
| `agentkit/storage/pg.py` | `PGTapeStore.truncate()` | ~5 lines |
| `coding_agent/ui/session_manager.py` | Checkpoint wiring + slash commands + restore flow + invalidation | ~200 lines |
| `coding_agent/cli/commands.py` | `@command("checkpoint")` — save + list (restore deferred) | ~40 lines |
| `coding_agent/cli/repl.py` | Inject `checkpoint_service` + `tape_store` into REPL context | ~5 lines |
| `tests/` | Truncate tests, checkpoint command tests, invalidation tests | ~150 lines |

---

## Explicit Non-Goals for Section 2B

1. **Automatic checkpoint policy** — v1 is explicit-only
2. **`/checkpoint delete`** — bookmarks are lightweight; no storage pressure
3. **Checkpoint fork (new tape_id on restore)** — truncate rollback chosen
4. **Cross-session checkpoint restore** — checkpoint is bound to its tape_id
5. **Checkpoint diff / compare** — not needed for v1
6. **HTTP API endpoints for checkpoint** — slash commands first; API is additive later
7. **REPL checkpoint restore** — requires pipeline lifecycle changes; save + list only in v1
8. **Shared REPL/HTTP command registry** — runtime shapes differ; converge later if needed

---

## Appendix: Deferred Concerns

The following items surfaced during 2A/2B design but do not require new
architecture. They are documented here as decisions, patterns, or pointers
to future work.

### A. Git ref restore UX

`_collect_extra()` already captures `git_ref` (HEAD SHA) at checkpoint
save time. On restore, the product layer should **not** auto-checkout:

- Working tree may have uncommitted changes → forced checkout = data loss
- The checkpoint's git ref may no longer exist (rebased, force-pushed)

Instead, restore should compare the current HEAD with the checkpoint's
`git_ref` and surface a warning if they differ:

```python
# In _restore_checkpoint(), after rebuild succeeds:
saved_ref = snapshot.extra.get("git_ref")
if saved_ref and session.repo_path:
    current_ref = _get_head_ref(session.repo_path)
    if current_ref and current_ref != saved_ref:
        logger.info(
            "Checkpoint was saved at git ref %.8s, current HEAD is %.8s. "
            "Consider `git diff %.8s` to review divergence.",
            saved_ref, current_ref, saved_ref,
        )
```

The user decides whether to checkout, stash, or ignore. This is a UX
hint, not an automated recovery mechanism.

### B. Plugin opt-in restore pattern

If a plugin wants to participate in checkpoint restore beyond the
default `ctx.plugin_states` hint injection:

1. **At save time**: ensure relevant state is JSON-serializable and
   stored in `ctx.plugin_states[plugin_key]`. The checkpoint's
   `extract_serializable_states()` will pick it up automatically.

2. **At restore time**: read from `ctx.plugin_states` during `mount()`
   or the first `on_turn_start()`. Accept that the value may be stale
   or absent (degraded continuity).

3. **For richer state**: use `snapshot.extra` via `_collect_extra()`.
   Product-layer code controls what goes into `extra`; plugins request
   inclusion by exposing a method like `checkpoint_extra() -> dict`
   that `_collect_extra()` calls.

This is a **convention**, not a framework protocol. No new base class,
no `RestorablePlugin` interface. Plugins that don't opt in get the same
degraded continuity as 2A's cold path — which is the correct default.

### C. Explicit non-goals (closed)

These were evaluated and deliberately excluded:

| Item | Reason |
|------|--------|
| Shell cwd/env restore | Shells are ephemeral; restoring cwd is fragile (dir may not exist), restoring env is dangerous (stale values). Degraded continuity is correct here. |
| Auto git checkout on restore | Risk of data loss; user should decide. See §A above. |
| Full runtime state restore | Fundamentally impossible without snapshotting OS-level resources (file descriptors, network connections, process trees). Not a goal. |
| `StatefulPlugin` protocol in agentkit | Rejected in Section 1. Plugin restore stays product-layer. |

### D. Future work (separate specs / PRs)

| Item | Scope | Depends on |
|------|-------|------------|
| REPL checkpoint restore | `ReplSession` lifecycle: mid-session tape replacement + idempotent `pipeline.mount()` | REPL pipeline lifecycle redesign |
| Multi-pod checkpoint restore | Shared `CheckpointStore` + `TapeStore` backend (PG); session lock to prevent concurrent restore | 2A v2 PG advisory lock |
| Shared command abstraction | Unified REPL/HTTP command dispatch if slash command count grows beyond checkpoint | Command count reaching ~5-6 |
| Checkpoint storage budget / GC | Garbage collection policy for old checkpoints; relevant only if auto-checkpoint is introduced | Auto-checkpoint policy decision |
| Auto-checkpoint policy | Product UX research on trigger granularity (every N turns, before destructive tools, on topic boundary) | User research + storage budget |
