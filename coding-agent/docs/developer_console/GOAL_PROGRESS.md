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

Status: merged via PR #262.

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

## G55 Developer Console ADR And UI Contract

Status: merged via PR #263.

### Intended Files

- `docs/adr/0037-developer-console-debug-ui.md`
- `docs/developer_console/UI_CONTRACT.md`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `test -f docs/adr/0037-developer-console-debug-ui.md`
- `test -f docs/developer_console/UI_CONTRACT.md`
- `rg -n "Developer Console debug UI boundary|server-rendered HTML|must never bypass approval|privacy-preserving|API Dependency Map|Observability Integration|Testing Strategy|Non-Goals" docs/adr/0037-developer-console-debug-ui.md`
- `rg -n "Route Contract|Navigation Contract|Data Contract|Action Contract|Testing Contract|must not render" docs/developer_console/UI_CONTRACT.md`
- `git diff --check -- .`

### Stop Criteria

- Stop if the console contract requires a new frontend build stack before the
  server-rendered MVP can be validated.
- Stop if the console contract requires changing AgentKit Core or G00-G53
  semantics.
- Stop if required pages cannot be defined without exposing raw sensitive data
  or bypassing approval/action policy.

### Changed Files

- `docs/adr/0037-developer-console-debug-ui.md`
- `docs/developer_console/UI_CONTRACT.md`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `test -f docs/adr/0037-developer-console-debug-ui.md`
- `test -f docs/developer_console/UI_CONTRACT.md`
- `rg -n "Developer Console debug UI boundary|server-rendered HTML|must never bypass approval|privacy-preserving|API Dependency Map|Observability Integration|Testing Strategy|Non-Goals" docs/adr/0037-developer-console-debug-ui.md`
- `rg -n "Route Contract|Navigation Contract|Data Contract|Action Contract|Testing Contract|must not render" docs/developer_console/UI_CONTRACT.md`
- `git diff --check -- .`

### Results

- ADR-0037 exists and defines the Developer Console as a Coding Agent
  debug/product surface over existing APIs and stores.
- ADR-0037 defines scope, supported pages, privacy/no-leak rules, API
  dependency map, observability integration, testing strategy, and non-goals.
- UI contract exists with route, navigation, data, action, rendering, and test
  contracts.
- `git diff --check -- .` passed.

### Remaining Risks

- G55 is documentation-only. The console shell, route rendering, and fixture
  tests remain for G56+.

## G56 Console Shell And Navigation

Status: merged via PR #264.

### Intended Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/ui/test_http_server.py -k "healthz or readyz or metrics" -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if minimal console shell requires a frontend framework or build system.
- Stop if shell routes require changing existing HTTP API/runtime behavior.
- Stop if no-secret rendering cannot be tested deterministically.

### Changed Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/ui/test_http_server.py -k "healthz or readyz or metrics" -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- Added a server-rendered Developer Console shell in Coding Agent UI code.
- Added `/console`, `/console/sessions`, `/console/runs`,
  `/console/interactions`, `/console/tape`, `/console/context`,
  `/console/memory`, `/console/actions`, `/console/observability`, and
  `/console/release`.
- Console navigation renders all required sections with empty-state content.
- Console route tests passed and verify no query-provided secret text, raw
  prompt/message markers, command output markers, stdout/stderr, or env markers
  are rendered.
- HTTP health/readiness/metrics scoped regression tests passed.
- Ruff format/check and `git diff --check -- .` passed.

### Remaining Risks

- G56 intentionally renders fixture/empty data only. Durable runtime, HITL,
  context, memory, action, observability, and release data views remain for
  G57-G63.

## G57 Sessions And Runs List

Status: passed local verification; pending PR.

### Intended Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if sessions/runs list requires changing durable runtime semantics.
- Stop if existing runtime APIs cannot provide deterministic fixture data.
- Stop if run list rendering requires exposing raw prompt/content/message,
  command output, stdout/stderr, env, or secrets.

### Changed Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- `/console/sessions` now renders recent visible session summaries with
  session ID, status, turn status, created/updated times, and current turn ID.
- `/console/runs` now renders visible durable runtime run summaries by walking
  visible sessions and reading existing runtime-store records.
- `/console/runs?status=...` filters runs by low-cardinality status without
  echoing arbitrary query text into the page.
- Run rows link to `/console/runs/{run_id}` for G58 detail work.
- Error summaries are rendered only through a sensitive-token redaction helper.
- Console tests cover fixture sessions, fixture runs, status filtering, empty
  states, and no raw prompt/content/message/command output/stdout/stderr/env or
  secret marker leakage.
- Durable runtime smoke, scoped ruff format/check, and `git diff --check -- .`
  passed.

### Remaining Risks

- Run detail links intentionally target the G58 page, which is not implemented
  in G57.
- Session/run list data remains limited to existing visible sessions; a global
  durable run index is not introduced in this goal.
