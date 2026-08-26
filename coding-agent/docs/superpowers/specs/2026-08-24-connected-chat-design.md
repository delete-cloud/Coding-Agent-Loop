# Connected Chat Design

**Date:** 2026-08-24  
**Decision:** ADR-0077  
**Contract:** `tests/fixtures/connected_chat/v1/connected-chat-contract.json`

## Product Contract

CAL becomes a connected Night Console over one canonical session timeline. The browser lists, selects, and revisits sessions by `?session=<id>`, loads bounded active history, sends/resumes/cancels a root turn, follows passive updates, and reconciles all delivery by stable logical identity. The product does not expose approvals, checkpoints, memory, diff, provider/model settings, detached execution, or login UX in this slice.

## Authority and Projection

**Canonical source:** session EventRecord is the sole canonical chat fact source. `EventRecord` in the session fact source is the only canonical chat record. Existing `commit_authoritative_uow` allocates `session_seq` and atomically writes session/run/event state in SQLite and PostgreSQL. Run-scoped runtime/display replay remains audit/debug and must not feed `GET /chat-events`.

Projection name is `connected-chat`; schema version is `1.0.0`. Active projection excludes events whose root run has ADR-0075 `superseded_by_checkpoint_id`; audit retains them. Restore advances projection epoch. Event kinds are closed for v1: `user_prompt`, `assistant_message`, `thinking`, `progress`, `tool_call`, `tool_result`, `root_terminal`. Additive payload fields are allowed; unknown event kinds fail parsing rather than disappear.

Every envelope contains `contract_version`, `source_event_id`, decimal-string `session_seq`, `session_id`, nullable `run_id`, `kind`, RFC3339 `created_at`, and typed `payload`. Tool results correlate by `call_id`; result-before-call is retained pending and merged when the call arrives. Duplicate call/result events do not create duplicate nodes.

## Cursor and Snapshot

The only cursor payload is `{v:1,kind:"chat",session_id,projection,epoch,after_seq,high_water_seq}`. The exact codec is `raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")` followed by `base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")`. Decode restores `=` padding, URL-safe-base64 decodes, parses UTF-8 JSON, validates exact fields/types, and asserts canonical re-encoding equals the original cursor byte for byte. It is typed opaque to clients: clients store/transmit it but do not construct or edit it.

A first snapshot captures current head H and returns `snapshot_cursor` with immutable `high_water_seq=H`. Each page is exclusive after `after_seq` and bounded at H. `next_cursor` changes only `after_seq`; it is null after H. Appends concurrent with pagination are invisible until a new snapshot/follow. Empty snapshots use both sequence fields `"0"`.

Errors are normative and fixture-backed: malformed 400; foreign session 409; expired 410/replay-required; wrong projection or epoch 409/replay-required; future 409. Error bodies use `{error:{code,message,retryable,replay_required?}}`.

## Replay-to-Follow Bridge

For GET follow, the server (1) validates cursor, (2) registers a bounded queue, (3) captures H, (4) replays `(after,H]`, (5) drops queued overlap `<=H`, and (6) emits queued records `>H`. Stable ID dedupe makes overlap harmless. Subscriber overflow, ownership loss, or sequence loss sends `stream_control` `{kind:"replay_required",reason,cursor:last_safe}` and closes. It never resumes after loss.

Delivery is at least once. Exactly-once claims apply only to the logical visible event after deterministic `source_event_id` dedupe.

## Commands and Lifecycle

Prompt admission validates auth/session/command, then one authoritative transaction persists exact untrimmed prompt text as `user_prompt`, admits the run, and records `command_id`. Validation/rejection writes nothing. A repeated command ID with identical input returns the original admission; conflicting input returns `409 command_conflict`.

Settled outcomes are `completed`, `failed`, `cancelled`, and `interrupted`. Exactly one `root_terminal` event is persisted per root run. In SQLite and PostgreSQL alike, one fenced authoritative UoW atomically commits final run state, final session state, and the `root_terminal` EventRecord. There is no non-atomic terminal escape hatch or best-effort second write. Crash recovery and cancel/disconnect/completion races retry that same idempotent fenced UoW. EOF and socket close are transport facts only.

| Trigger | Durable effect | Root terminal | Client action |
|---|---|---|---|
| normal completion | settle completed | completed | none |
| adapter failure | settle failed | failed | retry/Resume per error |
| explicit cancel | 202 cancelling, then settle | cancelled | Resume after settlement |
| owning POST disconnect | interrupt, then settle | interrupted | reload, then Resume |
| passive GET disconnect | none | none | reconnect from cursor |
| EOF without terminal | none | none | reconnect/reload |
| Resume | only after old run settled; create linked run | later outcome of new run | stream new run |

Cancel of no active turn returns `409 no_active_turn`. Resume of an unsettled run returns `409 resume_source_unsettled`. Resume never reuses process/run identity.

## HTTP and OpenAPI

- `GET /sessions/{session_id}/chat-events?cursor=&limit=` returns the bounded snapshot envelope.
- `GET /sessions/{session_id}/chat-events/follow?cursor=` returns passive SSE.
- `POST /sessions/{session_id}/prompt` accepts `{prompt,command_id}` and owns an SSE stream.
- `POST /sessions/{session_id}/resume` accepts `{command_id,parent_run_id,prompt}` and owns an SSE stream.
- `POST /sessions/{session_id}/cancel` returns checked JSON, normally 202.

SSE event names are `chat_event` and `stream_control`; `id` equals decimal `session_seq` for chat events. OpenAPI declares JSON schemas, all checked status responses, and `text/event-stream` content with the event envelope schema in descriptions/examples. No OpenRPC handler or code generation is introduced.

## Frontend Architecture

`webui/app-next/src/lib/connected-chat/wire.ts` validates fixture-shaped JSON; `client.ts` owns REST/fetch-SSE and API-base resolution; `timeline.ts` is a pure stable-ID reducer; `controller.ts` is framework-independent and generation-gates every async response; `use-connected-chat.ts` is the thin React adapter. Existing business components remain presentational.

A session selection increments generation, aborts old requests, closes details through existing raw query-param behavior, clears transient state, loads snapshot, then follows. Late responses with another generation are discarded. User drafts are removed only after admission is observed; J3 restores the draft and creates no phantom user event. Tool call/result order is independent; both duplicate and result-before-call cases converge.

Static export remains. Production base URL is same-origin; only `NEXT_PUBLIC_CODING_AGENT_API_URL` overrides it in development. Auth-disabled loopback sends no credentials. Enabled auth without credentials surfaces 401; there is no login UI.

Night Console constraints remain frozen: exact three vertical scroll regions, details mounted/inert when closed and closed on raw `?session` change, shadcn controls, literal next-intl keys with zh/en parity, current package dependency whitelist, no new dependency.

## Fixtures and Drift Control

Backend fixture is authoritative at `tests/fixtures/connected_chat/v1/connected-chat-contract.json`. Frontend mirror is `webui/app-next/test/fixtures/connected-chat/v1/connected-chat-contract.json` in the frontend worktree. Both include `contract_id`, semantic `contract_version`, and revision. They mechanically enumerate follow, all seven event kinds, all four terminal outcomes, all five cursor errors, named admission/lifecycle/auth errors, and exact `subscriber_queue_overflow`, `ownership_lost`, and `sequence_loss` stream-control reasons. Backend and frontend tests load their local copy; the cross-worktree gate runs `jq empty` and `python3 -m json.tool` on both, `cmp -s` for byte parity, SHA-256 comparison, and decode/canonical-re-encode byte assertions for every fixture cursor using the exact documented Python codec. Any semantic change increments version/revision and updates ADR/spec/tests together.

## Acceptance Journeys

J1 ordered prompt-to-terminal; J2 tool merge with duplicate/out-of-order delivery; J3 checked rejection/no phantom; J4 owning disconnect/interrupted/new linked Resume; J5 passive disconnect/no mutation/replay overlap dedupe; J6 cancel/202/one terminal; J7 explicit error and EOF non-terminal; J8 same-origin auth-disabled and enabled-auth 401.

## Delivery Gates

R0 approval freezes source, cursor, lifecycle, fixtures, errors, auth, and shell. ADR-0077 supersedes ADR-0076's P4-before-P5 ordering only for this REST/SSE slice; ADR-0076's later OpenRPC/P4 track remains preserved. After R0, backend R1/R2 and frontend fixture foundation may run in parallel. Real frontend adapter wiring waits until backend R2 canonical history, replay/follow, cancel, terminal, and auth tests are green. Deployment/browser acceptance remains a separate final gate.

## Non-Goals

No OpenRPC handlers/codegen; P4 daemon/socket/storage writer lease; remote loop or Bee revival; detached execution; approvals/checkpoints/memory/diff/provider-model/settings UI; login/cookie persistence; deferred UI; transport exactly once. These are exclusions, not deferred deliverables of this plan.
