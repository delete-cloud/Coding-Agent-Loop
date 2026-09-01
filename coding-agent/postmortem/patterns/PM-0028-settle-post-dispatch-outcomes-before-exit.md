---
id: PM-0028
title: Settle post-dispatch outcomes before exit
status: active
severity: high
confidence: high
subsystems:
- agentkit
- runtime
related_commits: []
related_files:
- src/agentkit/runtime/coordinator.py
- src/agentkit/runtime/contracts.py
- src/coding_agent/executors/durable.py
- src/coding_agent/runs/child_execution.py
- src/coding_agent/stores/local_durable/uow.py
- src/coding_agent/stores/pg_durable/uow.py
- tests/agentkit/runtime/test_coordinator.py
release_checks:
- Run the focused coordinator settlement-before-yield and executor-cancellation tests.
- Review every path after `DispatchPermit.claim()` for a completed, failed, or indeterminate settlement commit attempt before exit.
---

# Summary

Once a dispatch permit is claimed, external execution may have occurred. A control safe-yield, executor exception, or `asyncio.CancelledError` must not leave the durable effect at `dispatched` without a settlement attempt. Unknown execution is represented by an indeterminate settlement, never by omission.

# Trigger conditions

- A control generation rises after the executor returns but before settlement re-entry.
- The executor raises after `DispatchPermit.claim()`.
- The executor or coordinator task receives `asyncio.CancelledError` after dispatch.

- A process exits after an indeterminate settlement, and a fenced takeover must recover the effect without re-executing it.
# Known fix signals

- Settlement inputs bypass pre-commit safe-yield probes and are committed before control flow exits.
- Ordinary executor exceptions become `EffectIndeterminateResult` with a non-empty message.
- Post-dispatch task cancellation becomes an indeterminate settlement, commits, then propagates cancellation.
- Regression tests assert that `commit_settlement` occurs after execution and before safe-yield or cancellation propagation.
- Indeterminate settlement retains the effect plan and authorization while committing no terminal tool fact.
- Recovery requires exact durable evidence and either a quiescent executor attempt or an unclaimed attempt whose dispatch owner is fenced.
- Dispatch authorization atomically creates an `authorized_unclaimed` executor-attempt row; reserve, start, quiescence, and expired-reservation revocation are replay-safe.
- Owner-local child execution keeps the parent effect active across blocked and nonterminal safe-yield outcomes; cancel/interrupt quiesces child executors and settles claimed child permits before the parent returns.
- Recovered child terminal and parent-settlement writes require the active recovery lease identity, generation, and exact durable mailbox cut in the same authoritative transaction.

# Release review checklist

- Run `uv run pytest tests/agentkit/runtime/test_coordinator.py -k "settlement_commits_before_probe or crash_retains_run or executor_task_cancellation" -q`.
- Inspect all control and exception edges after permit claim. No edge may return, raise, or yield before settlement commit re-entry.
- Run `uv run pytest tests/coding_agent/test_runtime_phase_b_uow.py -k "executor or reconciliation or takeover" -q`.
- Run `uv run pytest tests/coding_agent/test_owner_local_child_execution.py tests/coding_agent/test_durable_effect_executor.py -q`.
- Run `uv run pytest tests/coding_agent/test_harness_p2_fact_source.py -k "child_binding or child_lease or recovery_guard or child_projection" -q`.
