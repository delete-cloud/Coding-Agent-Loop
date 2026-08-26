# Connected Chat Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the frozen app-next Night Console to canonical REST/SSE chat without leaking transport state into views.

**Architecture:** Parse the v1 mirror at the wire boundary, reduce all snapshot/live events through one stable-ID pure reducer, coordinate session generations in a framework-independent controller, and expose state through a thin React hook to existing presentational components.

**Tech Stack:** Next.js 16 App Router static export, React 19, TypeScript 5.9, Vitest 4, Testing Library, next-intl, shadcn/ui.

**Spec:** backend `docs/superpowers/specs/2026-08-24-connected-chat-design.md`

## Global Constraints

Use `webui/app-next/test/fixtures/connected-chat/v1/connected-chat-contract.json`. Keep static export, Night Console, exactly three scroll regions, literal i18n keys, shadcn controls, and current dependency whitelist. Same-origin by default; only `NEXT_PUBLIC_CODING_AGENT_API_URL` overrides development. No implicit localhost, login UI, deferred UI, new dependency, legacy app edits, commits, pushes, or PR steps.

---

### Task 1: Wire Contracts and Fixture Identity

**Files:**
- Create: `webui/app-next/src/lib/connected-chat/wire.ts`
- Create: `webui/app-next/src/lib/connected-chat/wire.test.ts`
- Test fixture: `webui/app-next/test/fixtures/connected-chat/v1/connected-chat-contract.json`

**Interfaces:**
- Produces: `ChatEventEnvelope`, `ChatSnapshot`, `ApiError`, `StreamControl`, `parseChatEvent`, `parseChatSnapshot`, `parseApiError`; all parsers return exact domain values or throw `ContractViolationError`.

- [ ] Write failing fixture-driven tests for follow, all seven event kinds, four terminal outcomes, five cursor errors, named admission/lifecycle/auth errors, exact `subscriber_queue_overflow`/`ownership_lost`/`sequence_loss` controls, decimal strings, stable IDs, cursor opacity, unknown kind rejection, and contract/version mismatch. Decode every fixture cursor and canonical re-encode with the documented sorted compact JSON algorithm, asserting byte-for-byte equality.
- [ ] Run `cd webui/app-next && pnpm vitest run src/lib/connected-chat/wire.test.ts`; verify red missing module.
- [ ] Implement explicit type guards without fallback defaults for required fields; preserve unknown additive payload fields.
- [ ] Run focused tests green.

### Task 2: REST and Fetch-SSE Client

**Files:**
- Create: `webui/app-next/src/lib/connected-chat/sse.ts`
- Create: `webui/app-next/src/lib/connected-chat/client.ts`
- Create: `webui/app-next/src/lib/connected-chat/client.test.ts`

**Interfaces:**
- Produces: `resolveApiBase(env, locationOrigin)`, `ConnectedChatClient` methods `listSessions`, `snapshot`, `follow`, `prompt`, `resume`, `cancel`; POST and GET streams both yield parsed domain envelopes.

- [ ] Write failing tests for CRLF/chunked/multiline SSE, comments, trailing frames, EOF non-terminal, same-origin default, explicit env override, no localhost guess, 401 taxonomy, cancel 202, and fixture POST/follow streams.
- [ ] Run focused Vitest and verify red.
- [ ] Implement fetch-based SSE for GET and POST with AbortSignal, checked JSON errors, and no native EventSource dependency.
- [ ] Run focused tests green.

### Task 3: Stable-ID Timeline Reducer

**Files:**
- Create: `webui/app-next/src/lib/connected-chat/timeline.ts`
- Create: `webui/app-next/src/lib/connected-chat/timeline.test.ts`

**Interfaces:**
- Produces: `TimelineState {order:string[], byId:Map<string,TimelineNode>, pendingToolResults:Map<string,ChatEventEnvelope>}` and pure `reduceChatEvent(state,event) -> TimelineState`.

- [ ] Write failing tests for duplicate snapshot/POST/follow delivery, replay overlap, assistant ordering by decimal `session_seq`, tool result-after-call, result-before-call, duplicate call/result, and one root terminal node.
- [ ] Run focused Vitest and verify red assertions demonstrate duplicate/ordering failures.
- [ ] Implement immutable dedupe by `source_event_id`, decimal-string comparison without Number conversion, and `call_id` correlation independent of arrival order.
- [ ] Run focused tests green using fixture overlap example.

### Task 4: Framework-Independent Session Controller

**Files:**
- Create: `webui/app-next/src/lib/connected-chat/controller.ts`
- Create: `webui/app-next/src/lib/connected-chat/controller.test.ts`

**Interfaces:**
- Consumes: `ConnectedChatClient`, timeline reducer.
- Produces: `ConnectedChatState`, `ConnectedChatController.selectSession/send/cancel/resume/dispose`, subscription API; no React imports.

- [ ] Write failing tests for J1–J7, snapshot-then-follow, reconnect from last safe cursor, replay-required state, owning POST abort interpreted as interrupted only after canonical reload, passive follow abort no mutation, draft restoration on J3, and EOF remaining non-terminal.
- [ ] Add controllable promises and write stale-generation tests: select A then B; late A snapshot/follow/prompt errors must not alter B state.
- [ ] Run focused Vitest and verify red generation and lifecycle behavior.
- [ ] Implement monotonic generation tokens, abort ownership, state transitions, canonical reload, and Resume only from durable interrupted/failed/cancelled terminal state.
- [ ] Run focused tests green.

### Task 5: React Adapter and Frozen Shell Wiring

**Files:**
- Create: `webui/app-next/src/hooks/use-connected-chat.ts`
- Modify: `webui/app-next/src/components/business/app-frame.tsx`
- Modify: `webui/app-next/src/components/business/sidebar.tsx`
- Modify: `webui/app-next/src/components/business/timeline.tsx`
- Modify: `webui/app-next/src/components/business/composer.tsx`
- Modify: `webui/app-next/src/components/business/session-bar.tsx`
- Modify: `webui/app-next/messages/zh.json`
- Modify: `webui/app-next/messages/en.json`
- Modify: `webui/app-next/src/components/business/app-frame.test.tsx`
- Create: `webui/app-next/src/components/business/connected-chat.test.tsx`

**Interfaces:**
- Produces: presentational props only; hook adapts controller; URL remains selection authority.

- [ ] Write failing component tests for real sessions, URL push/back selection, loading/empty/error/reconnecting/replay-required, send/cancel/interrupted/Resume, draft recovery, and stale-generation invisibility.
- [ ] Preserve and run existing shell tests red only for expected static fixture replacement: `pnpm vitest run src/components/business/app-frame.test.tsx src/components/business/connected-chat.test.tsx`.
- [ ] Wire hook and props minimally; keep details close-on-raw-query change, exact `.session-list`, `.timeline-scroll`, `.details-scroll`, and no transport types in components.
- [ ] Add literal zh/en keys for every new user-visible state/copy; no computed keys.
- [ ] Run focused tests green, then `pnpm verify` and `pnpm build`.

### Task 6: Contract and Acceptance Gates

**Files:**
- Create: `webui/app-next/src/lib/connected-chat/journeys.test.ts`
- Modify only if tests prove a gap: files owned by Tasks 1–5.

**Interfaces:**
- Produces: fixture-driven J1–J8 acceptance evidence.

- [ ] Write J1–J8 tests directly from fixture descriptions, including forced overlap/loss, concurrent reconnect, tool duplication/order, auth-disabled same-origin, enabled-auth 401, and EOF non-terminal.
- [ ] Run the journey file first and verify red before any corrective implementation.
- [ ] Make only the minimal Task 1–5 boundary correction that satisfies each red case.
- [ ] Run `pnpm vitest run src/lib/connected-chat src/components/business/connected-chat.test.tsx`, then `pnpm verify`, `pnpm build`, and the dependency whitelist check from the frozen design.

### Parallel and Integration Gate

Tasks 1–4 may proceed against approved v1 fixtures in parallel with backend R1/R2. Task 5 may use a fixture client but must not bind the real server until backend canonical snapshot, follow bridge, lifecycle, OpenAPI, and auth suites are green. Real browser J1–J8 and same-origin packaging occur after that backend gate. No owner-authorized commit step is included.
