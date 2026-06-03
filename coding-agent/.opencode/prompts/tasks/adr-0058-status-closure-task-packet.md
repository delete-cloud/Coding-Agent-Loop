Goal:
Close ADR-0058 follow-up status markers for implementation areas whose listed
subtasks are now complete.

Scope:
- Update fully completed ADR-0058 follow-up sections from `[~]` to `[x]`.
- Keep partially complete sections marked `[~]` when their body still lists a
  concrete remaining item.

Out of scope:
- Runtime code changes.
- Changing ADR decisions, accepted architecture, or deferred product paths.
- Pulling the untracked standalone `webui/` workspace into this branch.

Context:
- ADRs:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Target tests:
- `git diff --check`
- `git diff -- docs/adr/0058-local-daemon-control-plane-executor-architecture.md`

Loop policy:
- Engineer implements the smallest correct documentation change.
- Reviewer checks only whether the status markers match the ADR body.
- Reviewer reports only P1/P2 correctness or scope findings.

Stop conditions:
- Stop if a section still has a concrete remaining implementation item.
- Do not change the `DisplayEvent` status while the untracked `webui/` item
  remains listed.
