---
name: adr-first-workflow
description: Use when a task may need an ADR, target-test selection, or a bounded engineer-reviewer-verifier loop in this repository.
---

# ADR-First Workflow

## Overview

This repository defaults to ADR-first development, not spec-first development.

The long-lived artifacts are:

- code
- tests
- ADRs

Archived spec documents may remain in the repo as historical context, but they are not maintained.

## When To Use

Use this workflow when a task involves any of the following:

- persistence, protocol, or data-model changes
- cross-module boundary changes
- multiple reasonable implementation options with real trade-offs
- work that benefits from a bounded engineer/reviewer/verifier loop

Do not use this workflow for straightforward bug fixes, local refactors, or obvious implementation choices with no real design trade-off.

## ADR Decision Rule

Write or update ADR context before implementation when the task changes:

- persisted formats
- wire or protocol behavior
- module ownership or boundaries
- a choice future readers will ask "why" about

Do not write an ADR for:

- simple bug fixes
- isolated test repairs
- local code cleanup
- obvious internal implementation details

For the canonical ADR format, read `docs/adr/README.md`.

## Task Packet Rule

Before implementation, create a short task packet containing:

- goal
- scope
- out-of-scope items
- ADR paths
- relevant files
- target test commands
- stop conditions

Use `.opencode/prompts/task-packet.md` as the template.

If the task already matches a common pattern, start from one of these examples:

- `.opencode/prompts/examples/checkpoint-slash-command-task-packet.md`
- `.opencode/prompts/examples/review-fix-loop-task-packet.md`

## Bounded Loop

For non-trivial implementation, use this bounded loop:

1. Engineer implements the smallest correct change.
2. Engineer runs the target tests.
3. Reviewer reviews only the diff and affected tests.
4. Reviewer reports only `P1/P2` findings.
5. Engineer fixes only accepted `P1/P2` findings.
6. Engineer reruns the same target tests.
7. Verifier reruns the same target tests and reports pass/fail.
8. Stop.

## Stop Conditions

- At most one `review -> fix -> retest` cycle.
- If the task would change design boundaries, stop and ask the human.
- Ignore non-blocking optimization suggestions during the bounded loop.
- Reviewer does not edit code.
- Verifier does not suggest new work.

If you find yourself wanting a second open-ended review loop, that is usually a sign that the task needs a human scope decision.

## Choosing Target Tests

Choose the smallest commands that prove the task's intended behavior.

Prefer:

- the narrowest affected test file first
- one or two focused `-k` filters for regression tests
- the broader affected test suite only after the focused checks

Examples in this repo:

- `uv run pytest tests/agentkit/tape/test_store.py -v`
- `uv run pytest tests/cli/test_commands.py -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "checkpoint_restore or truncate" -v`

## Acceptance Criteria Rule

When an ADR needs acceptance criteria, make them executable.

Include:

- concrete test names
- the command that verifies them

Good pattern:

```md
## Acceptance Criteria
- [ ] `test_restore_preserves_stable_tape_id`
- [ ] `test_restore_deletes_checkpoints_ahead_of_restore_point`
- [ ] `uv run pytest tests/ui/test_session_manager_runtime.py -k "stable_tape_id or delete" -v`
```

## Quick Start

1. Decide whether the task needs an ADR.
2. If yes, link the ADR in the task packet.
3. Fill a task packet.
4. Run the bounded engineer/reviewer/verifier loop.
5. Stop after one review/fix/retest cycle.

## References

- `docs/adr/README.md`
- `.opencode/prompts/README.md`
- `.opencode/prompts/task-packet.md`
