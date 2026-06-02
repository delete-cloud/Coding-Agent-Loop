Goal:
Refresh ADR-0058 migration status after RunTarget authority and DisplayEvent
projection/replay endpoint slices landed.

Scope:
- Mark explicit `default_run_target` authority as landed while preserving
  `ExecutionBinding` compatibility.
- Record the current RuntimeEvent/DisplayEvent split status:
  projection model, service replay boundary, and additive HTTP replay endpoint.
- Keep remaining gaps focused on streaming/UI integration, store contracts,
  sandbox wrapper, daemon-backed clients, and runtime lifecycle extraction.

Out of scope:
- Change code or tests.
- Redesign the remaining migration plan.
- Mark LocalDaemonExecutor or daemon-backed clients complete.

Context:
- ADRs:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md
- Relevant files:
  - docs/adr/0058-local-daemon-control-plane-executor-architecture.md

Target checks:
- git diff --check

Loop policy:
- Engineer updates the status tracker.
- Reviewer reports only P1/P2 stale-architecture, contradiction, or scope
  issues.
- Engineer fixes only accepted P1/P2 findings.

Stop conditions:
- Stop if this needs code changes.
- Ignore wording-only optimization suggestions.
