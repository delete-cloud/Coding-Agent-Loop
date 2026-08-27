# ADR-0078: Recover unowned requested chat runs independently of SSE consume

**Status**: Accepted
**Date**: 2026-08-27

## Context

ADR-0077 admits a prompt and root run in one fenced unit of work, then streams
projections over SSE. The first implementation started the executor only after
the owning POST body iterator was consumed and only when `command_id` was not
idempotent. A crash or client disconnect in that window left `requested` plus
`turn_in_progress` with no executor. The same `command_id` then only followed
the idle stream. A fresh command was rejected.

Empty sessions also omitted `session_fact_source`, so `/chat-events/follow`
returned 404. Follow replay requested one 1000-event page and discarded
`next_cursor`, so idle history above one page hung. Admission did not publish
to subscribers already registered for the session.

## Decision

1. Executor launch is recoverable from durable admission. An owning stream
   starts the run when `command_id` is new, or when it is idempotent, the run
   is still `requested`, and this process has no live task for that `run_id`.
   A live task is never started twice.
2. The stream that created the task owns disconnect settlement. A second
   observer stream for the same command only follows.
3. Session creation and follow/snapshot with a missing fact row allocate
   `session_fact_source` at seq 0. A valid empty session is followable.
4. Follow replay pages until the captured high-water mark or an empty page.
   It must not enter live wait with unread history at or below that mark.
5. Every non-idempotent admission publishes the `user_prompt` to current
   session subscribers through the same in-process dispatch used for
   `root_terminal`.
6. Snapshot SQL selects only `CHAT_EVENT_KINDS` and stops after a bounded
   scan. Continuation stays in `next_cursor`.

## Alternatives Rejected

- Start the executor inside `admit_chat_command` — existing owning-stream
  tests patch `run_agent` after admit and would launch too early.
- Keep launch after the first follow event — still loses the consume window.
- Treat missing fact source as `session_not_found` — a freshly created
  authorized session is not missing.
- Emit `replay_required` after one page — idle history would never complete.

## Acceptance Criteria

- [ ] `test_idempotent_retry_starts_unowned_requested_run`
- [ ] `test_follow_empty_session_before_admission`
- [ ] `test_follow_pages_idle_history_above_page_size`
- [ ] `test_admit_publishes_prompt_to_existing_follower`
- [ ] `test_active_duplicate_command_observes_without_second_executor`
- [ ] `uv run pytest tests/ui/test_connected_chat_manager_seams.py tests/ui/test_connected_chat_follow.py tests/coding_agent/test_connected_chat_admission.py -q`

## References

- ADR-0077
- `src/coding_agent/server/session/manager.py`
- `src/coding_agent/server/session/persist.py`
- `src/coding_agent/stores/local_durable/fact_source.py`
- `src/coding_agent/stores/pg_durable/fact_source.py`
- `tests/ui/test_connected_chat_manager_seams.py`
