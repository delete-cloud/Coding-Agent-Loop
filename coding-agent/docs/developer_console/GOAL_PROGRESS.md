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

Status: merged via PR #265.

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

## G58 Run Detail And Event Replay

Status: merged via PR #266.

### Intended Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if run detail requires changing runtime replay semantics.
- Stop if message snapshot/event timeline cannot be represented without raw
  prompt/content/message/result/secret/text/command_output/stdout/stderr/env.
- Stop if deterministic fixture tests cannot cover completed, failed, running,
  event ordering, and no-leak rendering.

### Changed Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- Added `/console/runs/{run_id}` run detail page using existing visible runtime
  run, message snapshot, and runtime event replay APIs.
- Run detail renders metadata, status timeline fields, sanitized message
  snapshot summary, ordered runtime events, error summary, and related console
  links.
- Message snapshots are represented by role/type labels, message count,
  snapshot ID, timestamp, and safe metadata keys only.
- Runtime events are represented by sequence, kind, event ID, timestamp, and
  safe payload keys only.
- Page documents existing `/runs/{run_id}/events` replay behavior and
  `last_event_id` resume semantics.
- Tests cover completed, failed, running, event ordering, sensitive error
  redaction, and no raw prompt/content/message/result/secret/text/command
  output/stdout/stderr/env or tool-call payload leakage.
- Durable runtime smoke, pipeline stage/context baseline, scoped ruff
  format/check, and `git diff --check -- .` passed.

### Remaining Risks

- G58 keeps the page read-only and does not add live streaming. Links to tape,
  context, action, and observability detail pages are completed in later goals.

## G59 HITL Interaction Inbox

Status: merged via PR #267.

### Intended Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "approval or interaction" -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if interaction inbox requires bypassing approval/action policy.
- Stop if existing runtime interaction records cannot be listed
  deterministically from visible sessions/runs.
- Stop if rendering interactions requires raw request/response payload,
  command output, stdout/stderr, env, prompt/content/message/result/text, or
  secrets.

### Changed Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/ui/test_session_manager_public_api.py -k "approval or interaction" -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- `/console/interactions` now renders read-only pending and resolved
  interaction inbox sections.
- Inbox data is assembled from visible sessions, visible runtime runs, and
  existing runtime interaction records.
- Rows show interaction ID, linked run ID, session ID, tool call ID, kind,
  status, created time, and resolved time.
- Duplicate/terminal resolved states render explicitly in the resolved section.
- The console does not expose interaction request/response payloads or raw
  prompt/content/message/result/text/command output/stdout/stderr/env/secret
  markers.
- Existing approval resolution policy is unchanged; no resolve action is added
  in G59.
- Console tests, durable runtime smoke, approval-focused session manager tests,
  scoped ruff format/check, and `git diff --check -- .` passed.

### Remaining Risks

- G59 keeps HITL interactions read-only. A resolve UI can be added later only
  if it reuses existing approval endpoints and policy checks.

## G60 Tape And Context Inspector

Status: merged via PR #268.

### Intended Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_pack.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if tape/context inspection requires changing tape persistence or
  context-pack semantics.
- Stop if no deterministic fixture can represent tape info/search and context
  evidence without raw content.
- Stop if rendering requires raw prompt/content/message/result/text,
  command output, stdout/stderr, env, file contents, or secrets.

### Changed Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/session_manager.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_pack.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py src/coding_agent/ui/session_manager.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- `/console/tape` now renders optional tape debug info and tape search results
  through the existing `TapeDebugStore` extension when available.
- Tape search supports low-risk query filters for tape ID, kind, run ID, tool
  call ID, and anchor type.
- Tape debug access follows existing console visibility rules: admin can query
  globally, while user tokens are restricted to visible session/run tape IDs.
- Tape rows show sequence, kind, safe correlation IDs, anchor type, and safe
  payload/meta key names without raw payload values.
- `/console/context?run_id=...` now renders sanitized context-pack evidence
  summaries from existing run metadata when present.
- Context rows show section title, source kind, label, source ID, source path,
  line range, score, and evidence reason, with a link back to run detail.
- Empty/missing states are deterministic for both tape and context views.
- Console tests, durable runtime tape debug smoke, context-system smoke,
  context-pack tests, scoped ruff format/check, and `git diff --check -- .`
  passed.

### Remaining Risks

- Context inspector currently depends on context-pack summary data being
  present in durable run metadata or equivalent fixture data. It does not add a
  new persistence model for historical context packs.

## G61 Memory Action Validation Inspector

Status: merged via PR #269.

### Intended Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if memory/action/validation inspection requires new action execution,
  approval bypass routes, or changes to G00-G53 semantics.
- Stop if existing stores or run metadata cannot provide deterministic fixture
  summaries for memory evidence, action summaries, policy decisions, and
  validation results.
- Stop if rendering requires raw prompt/content/message/result/text,
  command output, stdout/stderr, env, file contents, patch contents, command
  strings, or secrets.

### Changed Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_validation_runner.py tests/coding_agent/action_safety/test_command_policy.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- `/console/memory?run_id=...` now renders read-only memory evidence from
  existing visible run metadata and context-pack memory entries.
- Memory rows show source ID, safe label, status, tag/evidence counts, source
  path, and line range without raw memory body or raw evidence text.
- `/console/actions?run_id=...` now renders action summaries from existing
  visible run metadata.
- Action rows show action ID, kind, status, policy decision, risk level,
  changed-path count, extension buckets, approval linkage/status, validation
  ID, and safe patch summary counts.
- Validation rows show safe validation labels, status, exit code, duration,
  policy decision, normalized failure summary counts, and a context link for
  failed validation investigation.
- Empty states are deterministic for missing run IDs, missing memory evidence,
  missing action summaries, and missing validation summaries.
- Memory and action pages use existing runtime-run visibility checks, with a
  regression test proving user tokens cannot inspect another owner's run.
- Local review found and G61 fixed two issues: context-pack memory labels are
  no longer rendered as prose in the memory page, and action extension buckets
  now accept the comma-separated string shape emitted by action observability.
- Console tests, safe action smoke, validation runner/command policy tests,
  scoped ruff format/check, and `git diff --check -- .` passed.

### Remaining Risks

- G61 reads memory/action/validation summaries from run metadata fixture shapes
  and existing context-pack summaries. It does not add a new persistence model
  or wire live action execution to produce every possible historical summary.
- Memory labels are rendered only after the existing console sanitizer, and
  metadata `summary` fields are intentionally not displayed by default.

## G62 Observability Release Integration

Status: merged via PR #270.

### Intended Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/ui/test_http_server.py -k "healthz or readyz or metrics" -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_local_stack.py tests/coding_agent/test_release_observability_contract.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if observability/release console integration requires external hosted
  services, production credentials, or changing G46-G53 observability
  contracts.
- Stop if rendering safe links requires exposing Langfuse keys, Grafana tokens,
  Prometheus credentials, or raw trace attributes.
- Stop if health, metrics, release verification, or dashboard status cannot be
  represented deterministically from local config and fixture data.

### Changed Files

- `src/coding_agent/ui/developer_console.py`
- `src/coding_agent/ui/http_server.py`
- `tests/ui/test_developer_console.py`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/ui/test_http_server.py -k "healthz or readyz or metrics" -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_local_stack.py tests/coding_agent/test_release_observability_contract.py -v`
- `uv run ruff format --check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `uv run ruff check src/coding_agent/ui/developer_console.py src/coding_agent/ui/http_server.py tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- `/console/observability` now renders safe run correlation metadata for
  visible runs: session ID, run ID, tape ID, retrieval ID, action ID,
  validation ID, and interaction ID.
- Observability backend status renders metrics endpoint state, tracing backend,
  metrics backend, and safe Langfuse/Grafana links when configured.
- Unsafe dashboard links with query strings, fragments, userinfo, or non-HTTP
  schemes are omitted.
- `/console/release` now renders local health/readiness summary and release
  verification gates from `docs/release_hardening/release-verification.yaml`.
- Local/no-Langfuse/no-Grafana modes degrade to explicit `not configured`
  states without external service calls or credentials.
- Console tests, health/readiness/metrics HTTP tests, observability local stack
  tests, release observability contract tests, scoped ruff format/check, and
  `git diff --check -- .` passed.

### Remaining Risks

- G62 does not validate live Grafana or Langfuse availability. It only renders
  configured safe links and local status, keeping external services optional.
- Release verification page displays the deterministic manifest commands but
  does not execute the release gate suite from the browser.

## G63 Developer Console E2E Smoke And Docs

Status: passed local verification; pending PR.

### Intended Files

- `tests/ui/test_developer_console.py`
- `docs/developer_console/USAGE.md`
- `docs/developer_console/IMPLEMENTATION_REPORT.md`
- `docs/developer_console/GOAL_PROGRESS.md`

### Verification Commands

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_local_stack.py tests/coding_agent/test_release_observability_contract.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/ui/test_developer_console.py`
- `uv run ruff check tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Stop Criteria

- Stop if final smoke requires new console features instead of only fixing
  regressions in the already implemented G54-G62 surface.
- Stop if prior smoke tests require external hosted services, production
  credentials, real LLM calls, or G00-G53 behavior changes.
- Stop if no deterministic fixture can demonstrate runtime, context, action,
  HITL, observability, release, and no-leak behavior together.

### Changed Files

- `tests/ui/test_developer_console.py`
- `docs/developer_console/USAGE.md`
- `docs/developer_console/IMPLEMENTATION_REPORT.md`
- `docs/developer_console/GOAL_PROGRESS.md`

### Tests Run

- `uv run pytest tests/ui/test_developer_console.py -v`
- `uv run pytest tests/integration/test_durable_runtime_smoke.py -v`
- `uv run pytest tests/coding_agent/test_context_system_smoke.py -v`
- `uv run pytest tests/coding_agent/action_safety/test_safe_action_smoke.py -v`
- `uv run pytest tests/coding_agent/test_observability.py tests/coding_agent/test_observability_local_stack.py tests/coding_agent/test_release_observability_contract.py -v`
- `uv run pytest tests/coding_agent/evaluation/ -v`
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "build_context or runtime_stage_spans" -v`
- `uv run ruff format --check tests/ui/test_developer_console.py`
- `uv run ruff check tests/ui/test_developer_console.py`
- `git diff --check -- .`

### Results

- Added a deterministic Developer Console E2E smoke test covering sessions,
  runs, run detail, HITL interactions, tape, context, memory, actions,
  validation, observability, release, navigation, and no-leak assertions.
- Added usage documentation for local route access, page purposes, privacy
  rules, and local observability links.
- Added final implementation report with G54-G63 landed goals, route inventory,
  acceptance audit, verification commands, and remaining risks.
- Prior durable runtime, context system, action safety, observability, release
  contract, evaluation, and AgentKit context/runtime span checks passed where
  practical.

### Remaining Risks

- G63 is a smoke/docs goal and intentionally adds no new runtime behavior.
- Full repository ruff remains outside the scoped console verification; touched
  files and whitespace checks passed.
