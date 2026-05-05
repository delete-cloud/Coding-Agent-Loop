Goal:
Close out ADR-0018 after PR1-PR4 by marking the decision accepted now that the documented implementation slices have landed.

Scope:
- Update `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md` status from `Proposed` to `Accepted`.
- Add a docs-only task packet that records this ADR closeout slice.
- Keep the body of the accepted ADR otherwise unchanged.

Out of scope:
- Do not change executable behavior, tests, or runtime code.
- Do not introduce a new ADR for distributed child workers in this PR.
- Do not revise historical rationale or acceptance criteria text beyond status closeout.

Context:
- ADRs:
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
  - `docs/adr/README.md`
- Relevant files:
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
  - `.opencode/prompts/tasks/adr-0018-pr5-acceptance-docs.md`

Target tests:
- `python3 - <<'PY2'
from pathlib import Path
text = Path('docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md').read_text()
assert '**Status**: Accepted' in text
print('ADR_STATUS_OK')
PY2`
- `uv run python -m compileall -q src tests/agentkit/runtime tests/coding_agent/tools tests/ui`

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
