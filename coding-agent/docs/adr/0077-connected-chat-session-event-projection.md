# ADR-0077: Use the session event log for connected chat

**Status**: Accepted
**Date**: 2026-08-24

## Context

ADR-0076 establishes the harness `EventRecord`, per-session decimal-string sequence, projection epoch, retention floor, and authoritative unit of work. ADR-0075 preserves all restored-away runs for audit while marking them superseded so active product views exclude them. ADR-0055 requires Resume to create a new linked run rather than reconnect an old process. The current HTTP UI instead rebuilds history from run-scoped display replay and exposes separate POST and GET streams. That shape cannot provide one stable logical identity, bounded snapshots, or reliable replay-to-follow behavior.

For this connected REST/SSE slice only, this record narrowly supersedes ADR-0076's P4-before-P5 ordering: the REST/SSE projection may proceed before P4. ADR-0076 remains phase authority for all other work, including its later OpenRPC/P4 daemon, socket, and lease track; that track is preserved and is neither cancelled nor reordered beyond this slice. Run replay remains useful for audit and debugging but cannot be a product fact source.

## Decision

1. Session `EventRecord` is the sole canonical chat fact source. `GET /sessions/{session_id}/chat-events`, prompt/resume POST SSE, and passive follow SSE are projections of the same records. Run event/display replay is audit/debug only.
2. The active projection excludes ADR-0075-superseded runs and their product events; audit APIs retain every record. Restore opens a new projection epoch. No record is deleted to repair active history.
3. Every projected event carries immutable `source_event_id = EventRecord.event_id` and `session_seq`, encoded as an unsigned decimal string. Identity and sequence are unchanged across snapshot, POST SSE, follow, overlap, and reconnect.
4. Transport is at least once. Consumers deterministically deduplicate logical events by `source_event_id`; no layer claims transport exactly once.
5. One opaque cursor type encodes `{v,kind,session_id,projection,epoch,after_seq,high_water_seq}` with the exact Python algorithm `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`, then `base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")`. Decoding restores padding, URL-safe-base64 decodes, parses UTF-8 JSON, validates the exact fields/types, and canonical re-encoding must equal the original cursor byte for byte. `snapshot_cursor` fixes an immutable high water. `next_cursor` is exclusive and advances only `after_seq`; every page returns `after_seq < session_seq <= high_water_seq`.
6. Cursor failures are exact: malformed `400 cursor_malformed`; foreign session `409 cursor_foreign_session`; below retention floor `410 cursor_expired` with `replay_required=true`; wrong projection epoch/name `409 cursor_wrong_epoch` with `replay_required=true`; above current head or high water `409 cursor_future`.
7. Follow uses register-before-replay: register bounded subscriber queue, capture high water H, replay `(cursor,H]`, discard queued overlap `<=H`, then emit `>H`. Queue overflow, ownership loss, or detectable sequence loss emits `stream_control/replay_required` with the last safe cursor and terminates. Silent continuation is forbidden.
8. Prompt text is persisted as a `user_prompt` event in the same authoritative unit of work that admits its root run. Failed admission creates neither. Idempotent `command_id` retries return the admitted run without a second prompt.
9. Each durably settled root outcome (`completed`, `failed`, `cancelled`, `interrupted`) has exactly one authoritative `root_terminal` logical event. Every final run state, final session state, and `root_terminal` EventRecord must commit together in one fenced authoritative UoW in both SQLite and PostgreSQL. No non-atomic or best-effort terminal path is permitted. Terminal persistence is idempotent under cancel/disconnect/completion races and crash recovery. EOF alone is never terminal.
10. The prompt/resume POST stream owns its run: disconnect interrupts and durably settles it. Passive GET follow is observer-only and disconnect never mutates a run. Resume is admitted only after the source run durably settles and creates a distinct run linked by `parent_run_id`.
11. Cancel returns `202` with `{contract_version,session_id,run_id,status:"cancelling"}` when work is active. Checked errors use `{error:{code,message,retryable}}`; OpenAPI documents every success/error response and SSE media type.
12. Nearest deployment is auth-disabled same-origin loopback. Static frontend uses same origin unless explicit `NEXT_PUBLIC_CODING_AGENT_API_URL` is set for development. No implicit localhost fallback exists. When auth is enabled and credentials are absent, protected routes return `401 credentials_required`; this phase adds no login UI.
13. The authoritative golden fixture is `tests/fixtures/connected_chat/v1/connected-chat-contract.json`. The frontend mirror is `webui/app-next/test/fixtures/connected-chat/v1/connected-chat-contract.json` in its worktree. `contract_id`, semantic `contract_version`, and `fixture_revision` identify it; CI must JSON-validate both and byte-compare them.

## Alternatives Rejected

- Joining run metadata and display replay: creates a second product fact source and cannot preserve identity across restore/reconnect.
- Updating ADR-0076: erases historical phase context and conflates OpenRPC/P4 scope with this HTTP slice.
- Timestamp or run-sequence cursors: cannot bind continuation to session, projection epoch, retention, and immutable high water.
- Replay then register: permits a gap between replay head and subscriber attachment.
- Unbounded queues or silent drops: convert load into invisible data loss.
- Treating disconnect/EOF as completion: confuses transport lifecycle with durable run truth.
- Reusing an interrupted run: violates ADR-0055 and pretends process continuity exists.

## Acceptance Criteria

- [ ] SQLite and PostgreSQL parity tests prove prompt/run admission atomicity, monotonic decimal sequences, idempotency, active supersession, bounded snapshots, and one terminal event.
- [ ] Concurrent snapshot tests append during pagination without leaking records above the captured high water.
- [ ] Cursor tests assert all five exact status/reason combinations and replay-required flags.
- [ ] Replay/follow tests prove register-before-replay overlap dedupe and explicit termination on queue overflow/loss.
- [ ] Lifecycle race tests prove owning disconnect interrupts, passive disconnect is inert, Resume waits for settlement, and EOF is non-terminal.
- [ ] HTTP/OpenAPI tests validate cancel/admission/error shapes, SSE schemas, and auth boundaries against the v1 fixture.
- [ ] Frontend tests consume the mirrored fixture and prove stale-generation rejection, stable-ID dedupe, tool call/result ordering, and J1–J8 states.
- [ ] Static export, Night Console, exactly three scroll regions, next-intl, shadcn usage, and dependency whitelist remain green.
- [ ] Exact backend contract tests pass: `test_fixture_covers_complete_connected_chat_contract`, `test_cursor_fixture_bytes_round_trip_canonically`, `test_cursor_error_taxonomy`, `test_prompt_admission_is_atomic_sqlite`, `test_prompt_admission_is_atomic_postgresql`, `test_terminal_uow_is_atomic_sqlite`, `test_terminal_uow_is_atomic_postgresql`, `test_terminal_uow_recovers_after_crash`, `test_terminal_races_write_one_root_terminal`, `test_follow_overflow_requires_replay`, `test_follow_ownership_loss_requires_replay`, `test_follow_sequence_loss_requires_replay`, `test_pm0021_registration_publication_race`, `test_pm0022_ownership_revalidation_race`, and `test_pm0023_idempotent_teardown_race`.
- [ ] Focused aggregate command passes: `uv run pytest tests/coding_agent/test_connected_chat_contract.py tests/coding_agent/test_connected_chat_projection.py tests/coding_agent/test_connected_chat_admission.py tests/ui/test_connected_chat_lifecycle.py tests/ui/test_connected_chat_follow.py tests/ui/test_connected_chat_http.py -q`.
- [ ] Release/postmortem aggregate command passes: `uv run pytest tests/coding_agent/test_harness_p2_fact_source.py tests/ui/test_http_server.py tests/ui/test_http_server_failover.py tests/ui/test_session_manager_runtime.py tests/ui/test_session_manager_public_api.py -k 'PM_0021 or PM_0022 or PM_0023 or registration or publication or ownership or teardown or connected_chat or clean or close or shut' -q`.
- [ ] `jq empty` succeeds for both fixtures, `cmp` reports them byte-identical, and every fixture cursor decodes and canonical re-encodes byte for byte with the Decision 5 Python algorithm.

## Implementation Plan

- Extend `src/coding_agent/stores/runtime_store.py` interfaces and both `src/coding_agent/stores/local_durable/` and `src/coding_agent/stores/pg_durable/` implementations; reuse `commit_authoritative_uow`, `EventRecord`, `ProjectionCursor`, and ADR-0075 run supersession fields.
- Add a connected-chat projector and cursor codec under `src/coding_agent/events/`; do not read run replay to construct canonical history.
- Add Pydantic contracts in `src/coding_agent/server/schemas.py` and routes in `src/coding_agent/server/http/routes/`; update route registration/OpenAPI tests in `tests/ui/test_http_server.py`.
- Add store/projector tests beside `tests/coding_agent/test_harness_p2_fact_source.py`; add concurrency and lifecycle tests in focused new test modules named by the design plan.
- In app-next, isolate wire parsing/client, pure timeline reducer, framework-independent controller, thin React hook, and existing `AppFrame`; no transport types enter presentational props.
- Execute the backend and frontend plans in `docs/superpowers/plans/2026-08-24-connected-chat-backend.md` and the frontend worktree equivalent. No implementation step includes commit/push/PR operations.

## Consequences

- Canonical product history becomes reconstructible from one append-only session log, while audit remains available through run APIs; this removes cross-run history joins from the UI path.
- Snapshot readers gain repeatable bounded views, at the cost of carrying opaque cursor state and explicitly restarting after retention/epoch changes.
- At-least-once overlap is expected and tested; backend and frontend must keep stable-ID dedupe logic, and queue pressure becomes a visible replay-required state rather than hidden loss.
- Admission and settlement perform more work in authoritative transactions. Implementers must keep owner fencing and SQLite/PostgreSQL parity tests around these paths; prompt admission and final run/session/root-terminal settlement each require one fenced authoritative UoW, with no best-effort or non-atomic second write.
- Owning and passive streams require separate teardown policies. Shared cleanup helpers may unregister resources but must not decide run mutation without stream ownership context.
- Cursor/schema changes are compatibility events: increment `contract_version` and `fixture_revision`, update both fixtures, OpenAPI examples, parsers, and tests in one reviewed change.
- Same-origin deployment simplifies the nearest loopback product but intentionally does not solve credential acquisition. Enabled auth remains unusable from this UI until a separately approved credential contract exists.
- The implementation adds no dependency. The primary risks are terminal double-write races, snapshot leakage above high water, and fixture drift; idempotent terminal keys, bounded queries, and byte-parity gates mitigate them.

## References

- ADR-0055: session Resume and interrupted-run semantics
- ADR-0075: active/superseded checkpoint timeline
- ADR-0076: harness control-plane fact source and UoW
- `docs/superpowers/specs/2026-08-24-connected-chat-design.md`
