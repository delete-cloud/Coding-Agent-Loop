# ADR-0079: Map tape, connected chat, CLI, and Night Console for the MVP

**Status**: Proposed
**Date**: 2026-09-04

Supersedes the 2026-08-26 Proposed body of this same file (stale checkout
facts). Does not supersede ADR-0077 or Accepted ADR-0085.

## Context

This repository is a personal coding-agent harness. Punch-tape (tape.systems /
DeepChat) separates three readers: the human's current conversation, the model's
per-turn assembled context, and the auditor's append-only facts.

As of `origin/main` `903c4db0` (2026-09-04):

- ADR-0077 is Accepted on main. Session `EventRecord` is the canonical chat
  fact source. Night Console lives at `webui/app-next`. Connected-chat routes
  and tests live under `src/coding_agent/events/` and `tests/coding_agent/`.
- ADR-0085 (Accepted) supersedes ADR-0083 and restages D–F. Phase G restore
  cutover is on main (PRs 736, 737, 738). Local dogfood on `agentkit-1` proved
  conversation, checkpoint, restore, reapprove, and one Night Console reply.
  Production `runtime_activation.new_sessions_enabled` stays off.
- ADR-0085 keeps Phases G and H from 0083: H is the later package extract, and
  it removes the legacy pipeline only after migration gates. This product map
  does not perform H or pipeline deletion. Accept this record and finish the
  MVP journey before any H ADR. Pipeline removal stays after productization.

The 2026-08-26 draft of this record claimed main still lacked connected-chat
and that the work lived only in dirty worktrees. That is false on current main.
This rewrite names the MVP product path against code that already exists, and
lists the remaining product gaps. It does not change persistence, wire schemas,
cursors, or units of work.

## Decision

### Tape

AgentKit `Tape` is the append-only runtime/context/checkpoint timeline
(ADR-0003: one stable `tape_id` per HTTP session). `TapeView` is the model's
derived window. `extract_turns` is another derived view. Legacy-runtime
checkpoint restore truncates tape to the captured entries. New-runtime
RestorePoints capture empty `tape_entries` (and empty plugin_states). Restore
still rewrites the session tape from that snapshot (DELETE then insert), so
the tape is wiped empty; chat is not rebuilt from tape. Chat rebuilds from
committed `EventRecord`s plus `OperationStateVersion` CAS, effects/mailbox,
and projection epoch. Tape is not the chat renderer.

Product UI must not label EventRecord / DisplayEvent history as "Tape".
A later Tape Inspector may read actual Tape entries. That inspector is out of
this MVP. `dispatch_committed` WAL is out of this MVP.

### Message flow (not a generic bus)

Four channels stay distinct:

1. Session `EventRecord` is durable chat truth (ADR-0077). Projector kinds in
   `CHAT_EVENT_KINDS`: `user_prompt`, `assistant_message`, `thinking`,
   `progress`, `tool_call`, `tool_result`, `approval_requested`,
   `root_terminal`.
2. HTTP transport of that projection has two shapes:
   - JSON snapshot: `GET /sessions/{id}/chat-events`
   - SSE: owning `POST .../prompt` and `POST .../resume` streams, plus
     `GET .../chat-events/follow`
   Delivery is at least once; clients dedupe by `source_event_id`.
3. Inbound control is not chat history:
   - `RuntimeMessageBus` is in-memory (legacy / in-process interrupt, steer,
     approval wake-up)
   - New-runtime durable control is `CommandMailbox` (ADR-0083/0085)
4. Run-scoped DisplayEvent / `RuntimeEventRecord` replay is audit/debug only.

Do not introduce a product "message bus". Copy may say "message flow" or
"Conversation". Architecture text names the actual channel.

Old `webui/app` DisplayEvent + client-side `pushUser` is the legacy GUI path.
It is not the MVP fact source.

### Alma completed / unfinished

Completed on current main:

- ADR-0077 connected-chat: session EventRecord, cursors, projector,
  `/chat-events` snapshot, prompt/follow SSE
- Night Console `webui/app-next` (static export + connected-chat controller)
- ADR-0085 D–F restage plus ADR-0083 G restore cutover, including
  restore-then-reapprove
- Empty `assistant_message` projection drop and pending-fact `run_id` stamp
  (PR 738)

Unfinished / not this MVP:

- HTTP prompt path still often omits `user_prompt` EventRecords unless
  `command_id` is set (Night Console create+send does; `GET /sessions` hides
  untitled sessions because title comes from `user_prompt`). Reload-from-events
  is incomplete until every product prompt writes `user_prompt` in the
  admission UoW.
- `serve` does not mount Night Console unless `WEBUI_DIST_DIR` points at an
  `app-next` export. Default `serve` is API-only.
- Production `new_sessions_enabled` remains 0. MVP dogfood is local SQLite
  via `daemon runtime-activation --enable` plus process restart, new sessions
  only.
- RestorePoint wire/GC/UI (later)
- ADR-0076 P3 OpenRPC / P4 unix socket / writer lease
- Tape Inspector; approvals/checkpoints/memory/diff as first-class Night
  Console panels
- `run --tui` is still in-process DisplayEvents. `daemon tui` already
  admits with `command_id` and consumes canonical `chat_event` SSE (the
  `event_format=display` query is ignored on that path), then adapts frames
  into the Rich Display consumer. See TUI section.
- Phase H ADR and code (after this record is Accepted and the MVP journey
  works)
- Legacy `Pipeline` deletion (after productization, per ADR-0085 H gates)

### MVP frontend run loop

The product path is Night Console (`webui/app-next`), not old `webui/app`.

Required journey:

1. Open Night Console against a local `serve`/`daemon` (dev: `next dev` with
   `NEXT_PUBLIC_CODING_AGENT_API_URL`, or `serve` with `WEBUI_DIST_DIR` set to
   the `app-next` export).
2. Create or select a session.
3. Submit one user prompt, persisted as `user_prompt` in the same UoW as run
   admission.
4. See the conversation/message flow (EventRecord projection: user, assistant,
   tools, approval, terminal).
5. After the root turn settles, the composer stays in the same session so a
   second user reply can be sent.

Reload must restore user prompt and assistant text from session events.

### CLI one-shot

For this MVP, keep the current split. Do not make one-shot the default:

- Bare `python -m coding_agent` and `repl`: interactive multi-turn (product
  dogfood).
- `run --goal`: labeled dev/testkit one-shot compatibility path. Scripts and
  `tests/cli/test_entrypoint_contract.py` still call it.
- `serve` / `daemon`: HTTP control plane.
- `daemon repl` / `daemon run`: HTTP interactive / one-shot.
- `daemon tui --goal`: HTTP Rich TUI against an already-running daemon.

Whether to delete `run --goal` later is **undecided**. This record does not
accept deletion and does not forbid a later ADR once no test or script
depends on it. MVP work must not delete it.

### TUI versus the EventRecord projection

MVP UI is Night Console over HTTP (`command_id` + JSON snapshot + chat SSE).

There are two Rich TUI entrypoints today:

- `run --tui`: in-process one-shot. It constructs a local runtime
  (`PipelineAdapter`) and renders DisplayEvents. It does not talk to `daemon`.
- `daemon tui --goal`: HTTP client of a running daemon. It sends `command_id`,
  so admission writes `user_prompt` and the stream is canonical `chat_event`
  SSE (`stream_chat_command`). The `event_format=display` query is ignored
  when `command_id` is set. The client then adapts `chat_event` frames into
  the Rich Display consumer. It cannot call `project_chat_event` in-process
  because the facts live in the daemon.

Unification lean (not this MVP): drop the in-process TUI so local and daemon
share one control plane. Interactive TUI becomes `daemon tui` (or equivalent)
against `serve`/`daemon`, same EventRecord projection as Night Console
(`GET /chat-events` snapshot + prompt/follow SSE). Whether to delete
`run --tui` is **undecided**; `tests/coding_agent/test_cli_pipeline.py` still
covers it. MVP work must not delete it.

If some in-process UI remains (for example a future REPL renderer), prefer
calling `project_chat_event` in-process rather than embedding an HTTP SSE
client in that process. That lean does not apply to `daemon tui`.

### Sequencing versus Phase H

Accept this record before drafting a Phase H ADR. Authority for H/pipeline
timing is ADR-0085, not this file: H extracts the host-neutral
`AgentEngine` + `SegmentCoordinator` package and removes the legacy pipeline
only after migration gates. This MVP does not start that work.

H must not redefine Tape, EventRecord, CLI defaults, or the Night Console
journey. Pipeline removal happens after this product map is Accepted and the
Night Console journey works.

Until this record is Accepted, do not change `src/agentkit`, `src/coding_agent`,
frontend, or tests for the MVP gaps above.

### Implementation Plan

After acceptance only:

- Persist `user_prompt` on the HTTP `/sessions/{id}/prompt` admission UoW the
  same way Night Console's create+send path does (`connected_chat.py` already
  knows the kind). Target: `src/coding_agent/server/http/routes/prompts.py`
  and the session persist/admission path it calls. Do not invent a second chat
  fact source.
- Mount or document the Night Console export on `serve` via `WEBUI_DIST_DIR`.
  Do not extend old `webui/app` for this MVP.
- Keep CLI labels in `src/coding_agent/cli/main.py`. Do not make `run --goal`
  the default. Do not delete `run --tui` in the same change.
- Do not change EventRecord schema, cursor codec, or UoW contracts.
- Do not start Phase H code or a Phase H ADR in the same change.

## Alternatives Rejected

- Render raw AgentKit Tape as the chat timeline — conflicts with ADR-0077 and
  needs a new projection/transport. Stop if this is required.
- Keep DisplayEvent/run replay as product history — cannot give one stable
  session identity or persisted user prompts (ADR-0077 rejected this).
- Delete `run --goal` in this MVP — still used as a testkit/CI seam; whether
  to delete it after that seam is gone is undecided (later ADR, not this one).
- Delete `run --tui` in this MVP — still covered by CLI tests; unification
  onto `daemon tui` is a later ADR.
- Build the MVP in old `webui/app` — forks Alma and keeps client-only user
  bubbles.
- Treat `RuntimeMessageBus` as the product bus — it is ephemeral inbound
  control.
- Put `daemon tui` on in-process `project_chat_event` — facts live in the
  daemon; that client must use the HTTP projection.
- Implement `dispatch_committed` WAL in this packet — persistence/protocol.
- Rewrite ADR-0076, ADR-0077, or ADR-0085 bodies — this record only maps
  product surfaces and defers H/pipeline deletion until after productization.
- Draft or implement Phase H in parallel with this MVP map — H waits.
- Flip production `new_sessions_enabled` as part of MVP — local dogfood only.

## Acceptance Criteria

- [ ] This file on main describes tape, message flow, Alma status, frontend
      loop, and CLI against current `origin/main` (connected-chat present;
      worktree-only story removed).
- [ ] Night Console (`webui/app-next`), not old `webui/app`, is named as the
      MVP UI path.
- [ ] `run --goal` remains a labeled one-shot; default CLI remains interactive.
- [ ] Visible EventRecord kinds include `approval_requested`.
- [ ] HTTP projection maps JSON snapshot vs SSE follow/prompt streams.
- [ ] After acceptance, HTTP prompt admission writes `user_prompt`:

```sh
uv run pytest tests/coding_agent/test_connected_chat_admission.py tests/coding_agent/test_connected_chat_projection.py tests/ui/test_connected_chat_http.py -q
```

- [ ] Focused backend gate (already on main; must stay green):

```sh
uv run pytest tests/coding_agent/test_connected_chat_contract.py tests/coding_agent/test_connected_chat_projection.py tests/coding_agent/test_connected_chat_admission.py tests/ui/test_connected_chat_lifecycle.py tests/ui/test_connected_chat_follow.py tests/ui/test_connected_chat_http.py tests/agentkit/tape/ tests/agentkit/runtime/test_runtime_messages.py -q
```

- [ ] CLI labels:

```sh
uv run pytest tests/cli/test_entrypoint_contract.py -k "daemon_repl_help or run_help_marks_command_as_dev_testkit_compatibility" -q
```

- [ ] Frontend focused journeys (from `webui/app-next`):

```sh
pnpm exec vitest run src/lib/connected-chat/journeys.test.ts src/components/business/connected-chat.test.tsx src/hooks/use-connected-chat.test.tsx
```

- [ ] Local `uv run python -m coding_agent serve --host 127.0.0.1 --port 8080`
      with Night Console available; browser: open UI → new session → one prompt
      → visible EventRecord message flow including the user text → composer
      accepts a follow-up. Reload still shows that user text.

## References

- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0076-harness-control-plane.md`
- `docs/adr/0077-connected-chat-session-event-projection.md`
- `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md` (superseded by 0085)
- `docs/adr/0085-restage-durable-runtime-activation-through-phase-f.md`
- `docs/web-client-api-contract.md` (legacy DisplayEvent GUI)
- `src/agentkit/tape/`
- `src/agentkit/runtime/messages.py`
- `src/coding_agent/events/connected_chat.py`
- `src/coding_agent/cli/main.py`
- `src/coding_agent/cli/serve_command.py` (`daemon tui`)
- `webui/app-next/`
- Punch-tape article: `给一艘正在航行的船装上打孔纸带.md`
- Alma handoff: `handoff-2026-08-25-post-r4-validation.md`
