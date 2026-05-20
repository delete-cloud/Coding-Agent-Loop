# Developer Console G54-G63 Goal Progress

Date started: 2026-05-20

## Phase Scope

This phase adds a Developer Console / Debug UI over existing Coding Agent APIs,
stores, metrics, and observability contracts. It must not reimplement or change
AgentKit Core, durable runtime, context retrieval, memory, action safety,
release hardening, or observability platform semantics.

## Global Constraints

- Keep AgentKit Core generic.
- Do not rewrite AgentKit pipeline.
- Do not change G00-G53 behavior.
- Do not implement schedules, desktop app, bridge, proactive agent, or full
  Docker sandbox in this phase.
- Do not require external services, production credentials, or real LLM calls.
- Do not expose secrets or raw sensitive content beyond existing sanitized API
  contracts.
- Do not bypass approval/action policy from console pages.
- Do not add raw prompt/content/message/result/secret/text/command output,
  stdout, stderr, or env values to tracing attributes or UI-visible metadata
  unless explicitly allowed by an existing sanitized API contract.

## Planned Goals

| Goal | Scope |
| --- | --- |
| G54 | Current-state UI/API/debug surface map. |
| G55 | Developer Console ADR and UI contract. |
| G56 | Minimal console shell and navigation. |
| G57 | Sessions and runs list. |
| G58 | Run detail and event replay. |
| G59 | HITL interaction inbox. |
| G60 | Tape and context inspector. |
| G61 | Memory, action, and validation inspector. |
| G62 | Observability and release integration. |
| G63 | E2E smoke and final docs. |

## G54 Developer Console Current State Map

Status: passed local verification; pending PR.

### Intended Files

- `docs/developer_console/CURRENT_STATE.md`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `test -f docs/developer_console/CURRENT_STATE.md`
- `test -f docs/developer_console/GOAL_PROGRESS.md`
- `rg -n "Recommended MVP: server-rendered HTML|No blocker found for G54|GET /runs/\\{run_id\\}/events|GET /metrics|PGTapeStore.info|AgentInteractionRecord" docs/developer_console/CURRENT_STATE.md`
- `rg -n "G54 Developer Console Current State Map|G55|G63|Do not change G00-G53 behavior" docs/developer_console/GOAL_PROGRESS.md`
- `git diff --check -- .`

### Stop Criteria

- Stop if an equivalent Developer Console already exists and this plan would
  duplicate it.
- Stop if the current UI/API surface cannot be mapped without production code
  changes.
- Stop if later console goals would require rewriting AgentKit Core or changing
  G00-G53 semantics.

### Changed Files

- `docs/developer_console/CURRENT_STATE.md`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `test -f docs/developer_console/CURRENT_STATE.md`
- `test -f docs/developer_console/GOAL_PROGRESS.md`
- `rg -n "Recommended MVP: server-rendered HTML|No blocker found for G54|GET /runs/\\{run_id\\}/events|GET /metrics|PGTapeStore.info|AgentInteractionRecord" docs/developer_console/CURRENT_STATE.md`
- `rg -n "G54 Developer Console Current State Map|G55|G63|Do not change G00-G53 behavior" docs/developer_console/GOAL_PROGRESS.md`
- `git diff --check -- .`

### Results

- Developer Console current-state document exists and maps the existing HTTP
  server, APIs, store capabilities, tests, response shapes, and likely future
  modification files.
- Progress ledger exists and includes G54-G63 scope and constraints.
- Evidence checks found the recommended server-rendered HTML MVP direction,
  absence of an equivalent console, runtime replay API, metrics endpoint, tape
  debug capability, and durable interaction record references.
- `git diff --check -- .` passed.

### Remaining Risks

- G54 is documentation-only. No console routes, UI contract, API helpers, or
  HTML rendering exist yet.
- Existing APIs do not yet expose global run listing, interaction inbox,
  tape debug, context inspector, memory evidence, or release verification pages
  through console routes.
