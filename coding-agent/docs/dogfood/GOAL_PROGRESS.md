# Dogfood + Demo Readiness Goal Progress

Date started: 2026-05-20

## Phase Scope

This phase validates the completed Coding-Agent-Loop platform on local,
repo-scoped dogfood tasks and produces a repeatable demo path. It must not
rewrite AgentKit Core, change G00-G63 contracts, add schedule/sandbox/desktop
or multi-agent work, require production credentials, or depend on external
hosted services.

Real dogfood completion requires run_id-level evidence. If real agent execution
cannot be performed with local configuration, the relevant goal must record a
blocker report explaining why.

## Planned Goals

| Goal | Scope | Expected result |
| --- | --- | --- |
| G64 | Current-state map and dogfood plan. | Document available local surfaces, evidence requirements, and deterministic demo plan. |
| G65 | Real dogfood execution evidence. | Execute one or more local repo tasks and record run_id-level evidence or a blocker report. |
| G66 | Repeatable demo readiness. | Add a deterministic demo guide/checklist that exercises runtime, console, observability, and release surfaces. |
| G67 | Final smoke and report. | Run practical regression checks and publish the implementation report. |

## G64_DOGFOOD_CURRENT_STATE_AND_PLAN

### Before

- Goal id: G64_DOGFOOD_CURRENT_STATE_AND_PLAN
- Intended files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/CURRENT_STATE.md`
- Verification commands:
  - Run from `coding-agent/`.
  - `test -f docs/dogfood/GOAL_PROGRESS.md`
  - `test -f docs/dogfood/CURRENT_STATE.md`
  - `rg -n "G64|G65|G66|G67|run_id-level evidence|repeatable demo path" docs/dogfood`
  - `git diff --check -- .`
- Stop criteria:
  - The repository has no deterministic local route to define a demo path.
  - The dogfood evidence requirement cannot be expressed without leaking raw
    prompt, content, result text, command output, stdout, stderr, env, or
    secrets.

### After

Status: passed local verification; pending PR.

- Changed files:
  - `docs/dogfood/GOAL_PROGRESS.md`
  - `docs/dogfood/CURRENT_STATE.md`
- Dogfood evidence:
  - G64 is documentation-only and does not claim real dogfood execution.
  - Real run_id-level evidence is explicitly deferred to G65.
- Tests run:
  - Run from `coding-agent/`.
  - `test -f docs/dogfood/GOAL_PROGRESS.md`
  - `test -f docs/dogfood/CURRENT_STATE.md`
  - `rg -n "G64|G65|G66|G67|run_id-level evidence|repeatable demo path" docs/dogfood`
  - `git diff --check -- .`
- Results:
  - All commands passed.
- Remaining risks:
  - G65 still needs to prove whether real local agent execution can produce
    run_id-level evidence without production credentials or external hosted
    services.
