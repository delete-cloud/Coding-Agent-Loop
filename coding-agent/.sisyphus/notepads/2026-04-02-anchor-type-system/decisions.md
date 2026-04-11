# 2026-04-02 Anchor Type System — Decisions

## 2026-04-10 closure re-check
- Final verdict: `minimal-tail-then-closure`.
- Task 3 executed to remove the import-cycle tail and restore a clean diagnostic state.
- Tape-view gate is authorized to open because anchor closure evidence proves all three required invariants:
  1. typed anchor dispatch
  2. anchor-aware tape/window behavior
  3. consumer-visible anchor handling
