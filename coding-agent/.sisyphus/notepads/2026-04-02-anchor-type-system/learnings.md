# 2026-04-02 Anchor Type System — Learnings

## 2026-04-10 closure re-check
- The closure proof is stronger when recorded as three layers: core type/serialization, tape/model behavior, and consumer/plugin behavior.
- Tape-view gating depends on explicit invariant evidence, not on plan-history assumptions.
- A passing test cluster was not sufficient for closure by itself; diagnostics exposed the import-cycle as the one real minimal semantic tail.
- After the Task 3 fix, the same test cluster plus clean diagnostics proved closure and preserved the required tape-view gate invariants.
