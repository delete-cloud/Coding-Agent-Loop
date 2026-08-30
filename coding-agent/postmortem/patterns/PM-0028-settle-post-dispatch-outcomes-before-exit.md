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

# Known fix signals

- Settlement inputs bypass pre-commit safe-yield probes and are committed before control flow exits.
- Ordinary executor exceptions become `EffectIndeterminateResult` with a non-empty message.
- Post-dispatch task cancellation becomes an indeterminate settlement, commits, then propagates cancellation.
- Regression tests assert that `commit_settlement` occurs after execution and before safe-yield or cancellation propagation.

# Release review checklist

- Run `uv run pytest tests/agentkit/runtime/test_coordinator.py -k "settlement_commits_before_probe or executor_exception or executor_task_cancellation" -q`.
- Inspect all control and exception edges after permit claim. No edge may return, raise, or yield before settlement commit re-entry.
