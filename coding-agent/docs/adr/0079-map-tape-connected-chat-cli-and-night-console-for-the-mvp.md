# ADR-0079: Map tape, connected chat, CLI, and Night Console for the MVP

**Status**: Proposed
**Date**: 2026-08-26

## Context

This repository is a personal coding-agent harness. Punch-tape (tape.systems /
DeepChat) separates three readers: the human's current conversation, the model's
per-turn assembled context, and the auditor's append-only facts. The current
checkout still serves chat from run-scoped DisplayEvent replay (`webui/app`).
User text is inserted client-side (`pushUser`) and is not restored on replay.

A later Alma refactor landed connected-chat R0–R4 in two dirty, uncommitted
worktrees, not on this checkout's `main`:

- Backend: `/Users/kina/Code/Agent/Coding-Agent-Loop/.worktrees/harness-p3-idl/coding-agent`
  (branch `feat/harness-p3-idl`, Accepted ADR-0077)
- Frontend: `/Users/kina/Code/Agent/Coding-Agent-Loop/.worktrees/webui-slice-1/coding-agent/webui/app-next`
  (branch `feat/webui-slice-1`, Night Console)

ADR-0077 already makes session `EventRecord` the sole canonical chat fact
source. ADR-0076 remains Proposed for the later OpenRPC / P4 daemon track.
This record does not change persistence, wire schemas, cursors, or units of
work. It names which existing surface is the MVP product path.

Current-checkout `main` (`cf2d895e`) does not contain connected-chat routes,
`EventRecord`, or `webui/app-next`. Implementing the MVP by extending old
`webui/app` would fork Alma and keep a second chat history.

## Decision

### Tape

AgentKit `Tape` is the append-only runtime/context/checkpoint timeline
(ADR-0003: one stable `tape_id` per HTTP session). `TapeView` is the model's
derived window. `extract_turns` is another derived view. Checkpoint restore
truncates the same tape. Tape is not the chat renderer.

Product UI must not label EventRecord / DisplayEvent history as "Tape".
A later Tape Inspector may read actual Tape entries. That inspector is out of
this MVP. `dispatch_committed` WAL is out of this MVP.

### Message flow (not a generic bus)

Four channels stay distinct:

1. Session `EventRecord` is durable chat truth (ADR-0077, worktree).
   Visible kinds: `user_prompt`, `assistant_message`, `thinking`, `progress`,
   `tool_call`, `tool_result`, `root_terminal`.
2. HTTP SSE is live transport of those projections (`/chat-events`,
   prompt/resume POST SSE, follow). Delivery is at least once; clients dedupe
   by `source_event_id`.
3. `RuntimeMessageBus` is in-memory inbound control (interrupt, steer,
   approval). It is not durable chat history.
4. Run-scoped DisplayEvent / `RuntimeEventRecord` replay is audit/debug only.

Do not introduce a product "message bus". Copy may say "message flow" or
"Conversation". Architecture text names the actual channel.

Current-checkout DisplayEvent + `pushUser` is the legacy GUI path. It is not
the MVP fact source.

### Alma completed / unfinished

Completed in the two worktrees, not on this `main`:

- Connected-chat plan R0–R4 (10/10)
- ADR-0077 Accepted: session EventRecord, cursors, register-before-replay,
  owning-POST disconnect interrupts, EOF is not terminal
- Night Console `webui/app-next` static export and connected-chat controller
- Post-R4 cleanup: backend `agent.toml` path, frontend flake classified P3

Unfinished / not shipped:

- Worktrees are dirty and uncommitted; no PR
- This checkout has no `chat-events` routes or `app-next`
- Night Console settings/theme/provider UI are non-goals; conversation uses
  server `agent.toml` plus API keys
- ADR-0076 P3 OpenRPC handlers, P4 unix socket / writer lease, Tape Inspector,
  approvals/checkpoints/memory/diff UI

### MVP frontend run loop

The product path is Night Console served from the connected-chat backend
(`serve` + `WEBUI_DIST_DIR` pointing at `webui/app-next` export), not old
`webui/app`.

Required journey:

1. Open the served Night Console.
2. Create or select a session.
3. Submit one user prompt (persisted as `user_prompt` in the same UoW as run
   admission).
4. See the conversation/message flow (EventRecord projection: user, assistant,
   tools, terminal).
5. After the root turn settles, the composer stays in the same session so a
   second user reply can be sent.

Reload must restore user prompt and assistant text from session events.

### CLI one-shot

Do not remove one-shot. Keep the split:

- Bare `python -m coding_agent` and `repl`: interactive multi-turn (product
  dogfood).
- `run --goal`: labeled dev/testkit one-shot compatibility path.
- `serve` / `daemon`: HTTP control plane.
- `daemon repl` / `daemon run`: HTTP interactive / one-shot.

The MVP does not make one-shot the default.

### Implementation ownership

Integrate the existing worktrees. Do not reimplement connected-chat on this
`main`. Do not edit old `webui/app` for this MVP. Do not change persistence or
protocol. Do not commit, push, or open a PR unless a human authorizes it.

## Alternatives Rejected

- Render raw AgentKit Tape as the chat timeline — conflicts with ADR-0077 and
  needs a new projection/transport. Stop if this is required.
- Keep DisplayEvent/run replay as product history — cannot give one stable
  session identity or persisted user prompts (ADR-0077 rejected this).
- Delete `run --goal` — removes a useful testkit seam for no MVP benefit.
- Build the MVP in old `webui/app` — forks Alma and keeps client-only user
  bubbles.
- Treat `RuntimeMessageBus` as the product bus — it is ephemeral inbound
  control.
- Implement `dispatch_committed` WAL in this packet — persistence/protocol.
- Rewrite ADR-0076 or ADR-0077 bodies — this record only maps product surfaces.

## Acceptance Criteria

- [ ] This file exists under `docs/adr/` with the five product blocks above
      (tape, message flow, Alma status, frontend loop, CLI).
- [ ] Night Console + connected-chat worktree, not old `webui/app`, is named as
      the MVP path.
- [ ] `run --goal` remains a labeled one-shot; default CLI remains interactive.
- [ ] Focused backend gate (from the integrated/backend worktree):

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
      with Night Console mounted; browser: open UI → new session → one prompt →
      visible EventRecord message flow → composer accepts a follow-up.

## References

- `docs/adr/0003-http-sessions-use-one-stable-tape-timeline.md`
- `docs/adr/0076-harness-control-plane.md`
- Worktree ADR-0077: `.worktrees/harness-p3-idl/coding-agent/docs/adr/0077-connected-chat-session-event-projection.md`
- `docs/web-client-api-contract.md` (legacy DisplayEvent GUI)
- `src/agentkit/tape/`
- `src/agentkit/runtime/messages.py`
- `src/coding_agent/cli/main.py`
- Punch-tape article: `给一艘正在航行的船装上打孔纸带.md`
- Alma handoff: `handoff-2026-08-25-post-r4-validation.md`
