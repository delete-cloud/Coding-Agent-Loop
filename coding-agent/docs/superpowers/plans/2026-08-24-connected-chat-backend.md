# Connected Chat Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement canonical session chat snapshots, reliable SSE follow, and checked root-turn lifecycle contracts.

**Architecture:** Project only authoritative session `EventRecord` records through a versioned connected-chat schema. Reuse the fenced authoritative UoW for admission/terminal writes; keep run replay audit-only. Separate store projection, cursor/bridge, command lifecycle, and HTTP schemas/routes.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, asyncio, SQLite, asyncpg-compatible PostgreSQL store, pytest/httpx/httpx-sse.

**Spec:** `docs/superpowers/specs/2026-08-24-connected-chat-design.md`

## Global Constraints

ADR-0077 and `tests/fixtures/connected_chat/v1/connected-chat-contract.json` are normative. Preserve ADR-0075 supersession and ADR-0055 Resume semantics. No OpenRPC/codegen, P4 daemon/socket/lease, remote loop/Bee, detached execution, deferred UI, production-code fixture shortcuts, commits, pushes, or PR steps.

---

### Task 1: Contract Models and Cursor Codec

**Files:**
- Modify: `src/coding_agent/stores/runtime_store.py`
- Modify: `src/coding_agent/server/schemas.py`
- Create: `src/coding_agent/events/connected_chat.py`
- Create: `tests/coding_agent/test_connected_chat_contract.py`

**Interfaces:**
- Produces: `ChatEvent`, `ChatEventKind`, `ChatSnapshot`, `ConnectedChatCursor`, `encode_chat_cursor(cursor) -> str`, `decode_chat_cursor(value, *, expected_session_id, fact_state) -> ConnectedChatCursor`.

- [ ] Write parameterized failing tests named `test_fixture_covers_complete_connected_chat_contract`, `test_cursor_fixture_bytes_round_trip_canonically`, and `test_cursor_error_taxonomy`; load v1, assert all event/error fields and decimal-string rules, decode every fixture cursor, and canonical re-encode with `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")` plus unpadded URL-safe Base64 to byte-for-byte equality.
- [ ] Run `uv run pytest tests/coding_agent/test_connected_chat_contract.py -q`; verify red failures are missing connected-chat symbols, not fixture parse failures.
- [ ] Implement frozen dataclasses/types and canonical sorted compact JSON + unpadded base64url codec; validate all bindings before constructing a cursor.
- [ ] Run the focused command again; verify all cases green.

### Task 2: Canonical Active Projection and Snapshots

**Files:**
- Modify: `src/coding_agent/stores/runtime_store.py`
- Modify: `src/coding_agent/stores/local_durable/fact_source.py`
- Modify: `src/coding_agent/stores/pg_durable/fact_source.py`
- Create: `tests/coding_agent/test_connected_chat_projection.py`

**Interfaces:**
- Consumes: Task 1 cursor/events.
- Produces: store protocol `snapshot_chat_events(session_id: str, cursor: ConnectedChatCursor | None, limit: int) -> ChatSnapshot`; projector `project_chat_event(record: EventRecord, run: AgentRunRecord | None) -> ChatEvent | None`.

- [ ] Write failing SQLite and fake-PG parity tests for empty/nonempty pages, immutable H while concurrent appends occur, exclusive continuation, restore epoch, retention expiry, and ADR-0075 superseded-run exclusion with raw audit retention.
- [ ] Run `uv run pytest tests/coding_agent/test_connected_chat_projection.py -q`; verify red on missing projection/store API.
- [ ] Implement projection reads directly from session event records, join only run visibility metadata for supersession filtering, and bound SQL by `(after_seq, high_water_seq]`; never read runtime/display replay.
- [ ] Run focused tests green, then `uv run pytest tests/coding_agent/test_harness_p2_fact_source.py tests/coding_agent/test_connected_chat_projection.py -q` for store parity.

### Task 3: Atomic Prompt Admission

**Files:**
- Modify: `src/coding_agent/stores/runtime_store.py`
- Modify: `src/coding_agent/stores/local_durable/uow.py`
- Modify: `src/coding_agent/stores/pg_durable/uow.py`
- Modify: `src/coding_agent/server/session_manager.py`
- Create: `tests/coding_agent/test_connected_chat_admission.py`

**Interfaces:**
- Produces: `admit_chat_command(session_id, *, prompt, command_id, parent_run_id=None) -> ChatCommandAdmission`; exact prompt event kind `user_prompt`; command conflict codes.

- [ ] Write failing parity tests proving exact prompt bytes, run row and prompt event share one transaction, rejection writes neither, identical command retry is idempotent, conflicting retry is 409-mappable, and Resume rejects until parent durably settles.
- [ ] Run `uv run pytest tests/coding_agent/test_connected_chat_admission.py -q`; verify red before implementation.
- [ ] Extend the existing authoritative UoW/receipt slot so command receipt, root run admission, session state, and prompt EventRecord commit atomically under owner authority.
- [ ] Run focused tests green and the Task 2 store suite green.

### Task 4: Terminal Persistence and Lifecycle Races

**Files:**
- Modify: `src/coding_agent/runs/lifecycle.py`
- Modify: `src/coding_agent/stores/runtime_store.py`
- Modify: `src/coding_agent/stores/local_durable/uow.py`
- Modify: `src/coding_agent/stores/pg_durable/uow.py`
- Modify: `src/coding_agent/server/session_manager.py`
- Modify: `src/coding_agent/server/http/routes/prompts.py`
- Modify: `src/coding_agent/server/http/routes/sse.py`
- Create: `tests/ui/test_connected_chat_lifecycle.py`

**Interfaces:**
- Produces: `settle_root_run(..., outcome: Literal["completed","failed","cancelled","interrupted"])`; one `root_terminal`; owning/passive disconnect distinction.

- [ ] Write failing SQLite/PostgreSQL parity and race tests named `test_terminal_uow_is_atomic_sqlite`, `test_terminal_uow_is_atomic_postgresql`, `test_terminal_uow_recovers_after_crash`, and `test_terminal_races_write_one_root_terminal`, covering completion-vs-cancel, cancel-vs-owning-disconnect, repeated finalization, owning POST disconnect interruption, passive GET disconnect no mutation, Resume-new-linked-run after settlement, and EOF without terminal.
- [ ] Run `uv run pytest tests/ui/test_connected_chat_lifecycle.py -q`; verify every behavior red for the intended reason.
- [ ] Route every settle path through one idempotent fenced authoritative UoW that atomically commits final run state, final session state, and one `root_terminal` EventRecord in both SQLite and PostgreSQL; no non-atomic or best-effort terminal escape hatch exists. POST generators shield this UoW on disconnect; GET generators only unregister subscribers.
- [ ] Run focused tests green, then `uv run pytest tests/ui/test_session_manager_runtime.py tests/ui/test_connected_chat_lifecycle.py -q`.

### Task 5: Register-Before-Replay Follow Bridge

**Files:**
- Modify: `src/coding_agent/server/http/events.py`
- Modify: `src/coding_agent/server/http/routes/sse.py`
- Create: `tests/ui/test_connected_chat_follow.py`

**Interfaces:**
- Produces: `follow_chat_events(session_id, cursor, queue) -> AsyncIterator[ChatEvent | StreamControl]`; `StreamControl(kind="replay_required", reason, cursor)`.

- [ ] Write failing deterministic tests named `test_follow_overflow_requires_replay`, `test_follow_ownership_loss_requires_replay`, and `test_follow_sequence_loss_requires_replay`; pause between register/capture/replay, append concurrently, assert overlap only, dedupe by stable identity, continue above H, and terminate with exact reasons `subscriber_queue_overflow`, `ownership_lost`, and `sequence_loss`.
- [ ] Run `uv run pytest tests/ui/test_connected_chat_follow.py -q`; verify red bridge ordering/loss behavior.
- [ ] Implement register → H → replay `(cursor,H]` → discard queued `<=H` → live `>H`; use bounded queue and last-safe cursor; never silently drop.
- [ ] Run focused tests green and postmortem regressions `uv run pytest tests/ui/test_http_server.py -k 'event_stream or disconnect or cleanup or teardown' -q`.

### Task 6: HTTP/OpenAPI and Golden Contract

**Files:**
- Modify: `src/coding_agent/server/schemas.py`
- Modify: `src/coding_agent/server/http/routes/sessions.py`
- Modify: `src/coding_agent/server/http/routes/prompts.py`
- Modify: `src/coding_agent/server/http/routes/sse.py`
- Modify: `src/coding_agent/server/http/routes/__init__.py`
- Create: `tests/ui/test_connected_chat_http.py`
- Test fixture: `tests/fixtures/connected_chat/v1/connected-chat-contract.json`

**Interfaces:**
- Produces: exact five endpoints and checked error envelope from the spec; OpenAPI response/media declarations.

- [ ] Write failing fixture-driven tests for snapshot, POST prompt/resume SSE, follow SSE, cancel 202/no-active error, every cursor/admission shape, enabled-auth 401, auth-disabled loopback, and OpenAPI examples/media/statuses.
- [ ] Run `uv run pytest tests/ui/test_connected_chat_http.py -q`; verify red routes/schemas.
- [ ] Add minimal Pydantic responses/routes and boundary exception mapping; retain original error causes in logs but expose only taxonomy; do not add OpenRPC.
- [ ] Run focused tests green; run `uv run pytest tests/coding_agent/test_connected_chat_contract.py tests/coding_agent/test_connected_chat_projection.py tests/coding_agent/test_connected_chat_admission.py tests/ui/test_connected_chat_lifecycle.py tests/ui/test_connected_chat_follow.py tests/ui/test_connected_chat_http.py -q`.
- [ ] Run affected regression gate `uv run pytest tests/coding_agent/test_harness_p2_fact_source.py tests/ui/test_http_server.py tests/ui/test_session_manager_runtime.py -q` and record exact totals/failures without fixing unrelated failures.

### Task 7: PM-0021/PM-0022/PM-0023 Release Races

**Files:**
- Modify: `tests/ui/test_http_server.py`
- Modify: `tests/ui/test_session_manager_runtime.py`
- Test: `tests/ui/test_connected_chat_follow.py`
- Test: `tests/ui/test_connected_chat_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 4–6 stream registration, owner fencing, and teardown behavior.
- Produces: named release regressions for PM-0021 registration/publication ordering, PM-0022 ownership revalidation, and PM-0023 idempotent cleanup/teardown.

- [ ] Add `test_pm0021_registration_publication_race`: deterministically pause registration before publication, race an append, and assert no gap or duplicate visible logical event.
- [ ] Add `test_pm0022_ownership_revalidation_race`: transfer/lose ownership between validation and mutation, assert exact `ownership_lost` replay control, and prove stale ownership cannot settle a run.
- [ ] Add `test_pm0023_idempotent_teardown_race`: race disconnect, cancellation, and duplicate cleanup; assert one unregister, one fenced settlement for owning POST, and no settlement for passive GET.
- [ ] Run `uv run pytest tests/ui/test_http_server.py tests/ui/test_session_manager_runtime.py tests/ui/test_connected_chat_follow.py tests/ui/test_connected_chat_lifecycle.py -k 'pm0021 or pm0022 or pm0023' -q`; verify red before implementation and green after the minimal fixes.
- [ ] Run release aggregate `uv run pytest tests/coding_agent/test_harness_p2_fact_source.py tests/ui/test_http_server.py tests/ui/test_http_server_failover.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py -k 'PM_0021 or PM_0022 or PM_0023 or registration or publication or ownership or teardown or connected_chat or clean or close or shut' -q` and record exact totals/failures.

### Backend/Frontend Gate

Frontend fixture foundation may run after R0 contract approval. Real adapter wiring may start only when Tasks 1–6 focused suites are green, OpenAPI matches fixture v1, and backend/frontend fixture files are byte-identical. No owner-authorized commit step is included.
