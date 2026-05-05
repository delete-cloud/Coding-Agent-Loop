Goal:
Accept ADR-0018 now that PR1-PR4 landed and its documented acceptance criteria have been implemented and verified.

Scope:
- Update `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md` status from `Proposed` to `Accepted`.
- Keep the ADR body immutable otherwise.
- Record this docs-only closeout as the ADR-0018 PR5 task packet.

Out of scope:
- Do not change ADR-0018 technical content, acceptance criteria, or references.
- Do not introduce any new runtime, subagent, session, or HTTP behavior.
- Do not reopen distributed child-worker design; that remains a future ADR.

Context:
- ADRs:
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
  - `docs/adr/README.md`
- Relevant files:
  - `docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md`
  - `.opencode/prompts/tasks/adr-0018-pr5-accept-status.md`

Target tests:
- `python3 - <<'PY2'
from pathlib import Path
text = Path("docs/adr/0018-owner-local-cloud-aware-subagent-orchestration.md").read_text()
assert "**Status**: Accepted" in text
assert "**Status**: Proposed" not in text
print("ADR_STATUS_OK")
PY2`
- `uv run python -m compileall -q docs 2>/dev/null || true`

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
