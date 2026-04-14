# ADR-0007: Task packets are the verification contract

**Status**: Accepted
**Date**: 2026-04-14

## Context

This repository already uses task packets under `.opencode/prompts/` as the per-task artifact that drives the bounded engineer/reviewer/verifier loop.

We need a reusable verification mechanism that supports both agent-run verification and human-readable checklists, without introducing a second per-task artifact that can drift away from the task packet.

The existing `verifier.md` prompt reruns the exact target tests from the task packet, but there is no first-class runtime implementation that can parse a task packet and turn it into a structured verification run or printable checklist.

## Decision

Task packets are the canonical verification contract in v1.

The first implementation slice normalizes the `Target tests:` section from a task packet into a structured verification contract inside `coding_agent`.

The runtime implementation lives in `src/coding_agent/verification/` and is exposed through a new CLI entrypoint, `python -m coding_agent verify --task-packet <path> --mode run|checklist`.

In v1, verification steps are limited to single commands. The runner executes them sequentially and reports either:

- machine-oriented verification output (`VERIFIED` / `NOT VERIFIED`), or
- a human-readable checklist derived from the same contract.

Separate sidecar `.verify/*.yaml` files are explicitly deferred. If richer verification semantics are needed later, they should be added inside the task packet first, not as a second co-equal artifact.

## Alternatives Rejected

- Use `.verify/<feature>.yaml` as the primary source of truth — rejected because it duplicates the task packet and creates drift between execution instructions and verification instructions.
- Add a first-class agent tool before a CLI runner exists — rejected because it expands the runtime and approval surface before the contract format and execution path are stable.
- Keep verification prompt-only with no runtime parser — rejected because it prevents consistent machine execution and checklist rendering from the same source.

## Acceptance Criteria

- [ ] `test_verify_checklist_renders_target_tests_from_task_packet`
- [ ] `test_verify_run_executes_target_tests_from_task_packet`
- [ ] `test_verify_run_reports_not_verified_when_command_fails`
- [ ] `uv run pytest tests/cli/test_verify.py tests/coding_agent/test_verification.py -v`

## References

- [`AGENTS.md`](../../AGENTS.md)
- [`.opencode/prompts/task-packet.md`](../../.opencode/prompts/task-packet.md)
- [`.opencode/prompts/verifier.md`](../../.opencode/prompts/verifier.md)
- [`src/coding_agent/__main__.py`](../../src/coding_agent/__main__.py)
