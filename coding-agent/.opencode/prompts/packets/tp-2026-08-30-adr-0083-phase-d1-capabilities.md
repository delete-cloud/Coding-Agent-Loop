# Task Packet

packet_id: tp-2026-08-30-adr-0083-phase-d1-capabilities
packet_revision: 1
role: implementer
baseline_ref: origin/main
baseline_sha: 1ce2214af3a52e5febb28a5b2960e6edef442284
branch: feat/adr-0083-phase-d

## Goal

Implement the first internal ADR-0083 Phase D cutover slice: enforce an explicit plugin capability boundary, move core tool execution and `Environment` ownership behind a host-owned executor, and remove automatic retries from the legacy `Toolset` execution path.

## Scope

- Add an explicit capability classification for plugins registered as pending-fact contributors, effect-plan/schema producers, or observers.
- Reject capability-declared plugins that expose host-only hooks such as storage, model, approval interaction, or tool execution.
- Keep legacy plugins operable until their own Phase D slices, but make the migrated core-tools path capability-safe.
- Split core tool metadata from execution. `CoreToolsPlugin` exposes immutable schemas only. A host-owned executor owns `Environment`, executable file/shell/web/subagent functions, shell-session mutation, and execution dispatch.
- Inject the host executor into the internal `Pipeline`/`Toolset` compatibility path so existing core tool behavior and tool filtering remain intact.
- Route `ParallelExecutorPlugin` through the host executor, never through `CoreToolsPlugin`.
- Remove `ToolExecutionOptions.max_retries` and all `Toolset` retry loops. One legacy dispatch attempt produces one result or error; model-provider retries remain unchanged.

Allowed production files:

- `src/agentkit/plugin/registry.py`
- `src/agentkit/plugin/__init__.py`
- `src/agentkit/runtime/hook_runtime.py`
- `src/agentkit/runtime/pipeline.py`
- `src/agentkit/tools/toolset.py`
- `src/agentkit/tools/__init__.py`
- `src/coding_agent/plugins/core_tools.py`
- `src/coding_agent/core/app.py`

Allowed tests are the matching plugin registry, hook runtime, toolset, core-tools, bootstrap, cloud-environment, parallel-executor, and subagent tests.

## Authority

- `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`, especially lines 20-65, 102-115, and 183-189.
- `postmortem/patterns/PM-0001-address-code-review-issues.md`
- `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
- `postmortem/patterns/PM-0018-restore-child-pipeline-bootstrap-wiring.md`
- `postmortem/patterns/PM-0028-settle-post-dispatch-outcomes-before-exit.md`

## Non-goals

- No semantic-memory grounding migration in this slice.
- No durable `CommandMailbox`, approval/subagent command migration, or `settled` alias/rank removal; those must cut over atomically in the next persistence slice.
- No `CommitPort` or durable `EffectExecutor` implementation for SQLite/PostgreSQL (Phase E).
- No canonical `EventRecord` wire cutover (Phase F).
- No coordinator recovery/reconciliation change and no Phase C public signature change.
- No provider/model retry change.

## Acceptance criteria

- A capability-declared plugin cannot register `provide_storage`, `provide_llm`, `approve_tool_call`, `execute_tool`, `execute_proxy_tool`, or `execute_tools_batch`.
- `CoreToolsPlugin` has no `Environment`, executable registry, shell-session, web backend, child-pipeline builder, or execute method.
- The host executor owns core tool construction and execution; the compatibility pipeline injects it into `Toolset` without exposing it to plugins.
- Core tool schemas, filtering, sync/async execution, shell-session updates, subagent bootstrap, and cloud environment behavior remain covered by focused tests.
- `ParallelExecutorPlugin` calls the host executor directly.
- Toolset invokes a host/plugin execution function at most once even when it raises.
- `ToolExecutionOptions` has no retry count and `Pipeline` no longer reads `tool_max_retries`.
- Phase C public runtime signatures and exports remain unchanged.
- Focused tests, full `tests/agentkit/`, impacted coding-agent tests, CLI tests, Ruff, and postmortem release checks pass.
- One bounded P1/P2 review, at most one accepted-fix pass, and one verifier retest complete.

## Stop conditions

- Stop if implementation requires changing a frozen Phase C request, proposal, outcome, port, or result signature.
- Stop if core tool execution requires implementing Phase E durable host ports.
- Stop after one `review -> accepted fixes -> retest` cycle.
