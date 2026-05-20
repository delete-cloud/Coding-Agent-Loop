# Workspace Provider / Sandbox MVP Implementation Report

Date: 2026-05-21

## Summary

G68-G76 completed the Workspace Provider / Sandbox MVP phase without rewriting
AgentKit Core, changing G00-G67 semantics, requiring production credentials, or
making Docker mandatory for deterministic tests.

The phase made workspace provider identity explicit, proved selected workspace
bindings route action tools to the chosen environment, hardened durable
workspace lifecycle metadata, added safe console visibility, and recorded a
repeatable local dogfood/demo path.

## Landed Goals

| Goal | Result |
| --- | --- |
| G68 | Current-state map landed in PR #276. |
| G69 | Workspace provider and sandbox boundary ADR landed in PR #277. |
| G70 | Execution binding provider metadata hardening landed in PR #278. |
| G71 | Workspace action routing proof landed in PR #279. |
| G72 | Provider capability reporting landed in PR #280. |
| G73 | Workspace lifecycle API and durable metadata hardening landed in PR #281. |
| G74 | Developer Console workspace visibility landed in PR #282. |
| G75 | Workspace provider dogfood/demo path landed in PR #283. |
| G76 | Final smoke and implementation report are prepared in this PR. |

## Key Artifacts

- `docs/workspace_provider/CURRENT_STATE.md`
- `docs/adr/0038-workspace-provider-and-sandbox-mvp-boundaries.md`
- `docs/workspace_provider/GOAL_PROGRESS.md`
- `docs/workspace_provider/RUN_EVIDENCE.md`
- `docs/workspace_provider/DEMO_PATH.md`
- `tests/coding_agent/test_workspace_action_routing.py`
- `tests/dogfood/test_workspace_provider_demo.py`
- `tests/ui/test_developer_console.py`

## Acceptance Summary

- AgentKit Core remains provider-neutral.
- Existing durable runtime, context, action safety, release, observability,
  console, and dogfood contracts were preserved.
- Workspace provider metadata now round-trips through local and cloud execution
  bindings.
- Workspace provider capabilities are deterministic and do not require Docker.
- Provider-local lifecycle operations fail closed for foreign provider
  instances.
- Developer Console exposes a read-only Workspaces page using sanitized existing
  workspace API/store data.
- Prometheus metrics use stable route labels and do not include workspace ids or
  `workspace_id` labels.
- G75 recorded run_id-level and workspace_id-level local dogfood evidence in
  `docs/workspace_provider/RUN_EVIDENCE.md`.
- The required demo path is deterministic and does not require Docker,
  production credentials, hosted services, or real external LLM calls.

## Verification

Final G76 verification is recorded in `docs/workspace_provider/GOAL_PROGRESS.md`.
All final smoke commands passed. The practical final smoke included:

- workspace provider dogfood replay
- prior local dogfood replay
- workspace action routing
- Developer Console smoke
- Docker provider capability/readiness checks with fakes
- durable workspace lifecycle hardening checks
- observability platform smoke
- release-hardening regression gates for durable runtime, context system, action
  safety, evaluation, and AgentKit runtime context pipeline
- `git diff --check -- .`

## Residual Risks

- Live Docker provider demos remain optional and environment dependent. The
  required proof uses fake/local providers and temp/in-memory stores.
- PostgreSQL-backed workspace metadata is covered through existing store and HTTP
  tests, but the G75 replay itself uses an in-memory metadata store.
- Full repository ruff remains outside this phase because prior phases recorded
  unrelated existing all-repo lint/format failures; this phase used scoped
  checks where Python files were touched.
