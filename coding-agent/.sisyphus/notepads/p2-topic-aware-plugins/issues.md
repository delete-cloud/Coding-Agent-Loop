# P2 Issues / Gotchas

## 2026-03-30

### Tape windowing bug (NOT a blocker for P2 plugin unit tests)
`Tape.handoff()` sets `_window_start = len(self._entries) - 1` (anchor becomes last entry),
so all recent entries disappear from windowed view. This is a pipeline runtime bug — it does
NOT affect plugin unit tests because tests call `tape.windowed_entries()` directly on fresh tapes
with no prior window advancement.

Tasks 1, 2, 3 are pure unit tests for plugin logic — they are unaffected by the windowing bug.

## 2026-04-10 — Closure status

- No open implementation gaps were found during closure verification.
- One documentation drift remains worth noting:
  - the plan's smoke example says the summarizer split point should be `12`,
  - but the current implementation, tests, and live smoke all agree on `split_point == 8` for the covered scenario.
- This is treated as plan/example drift, not as a product issue.
