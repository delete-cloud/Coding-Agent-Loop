Goal:
- Add the Phase 1 postmortem onboarding command and generate the initial `./postmortem/` artifact set from historical fix commits.

Scope:
- Add deterministic git-history collection and clustering in `coding_agent`.
- Add a CLI entrypoint at `python -m coding_agent postmortem phase1`.
- Generate `postmortem/README.md`, `taxonomy.yaml`, `index.yaml`, onboarding reports, templates, and initial pattern documents.
- Add focused regression tests for the builder and CLI command.

Out of scope:
- Release-time risk matching or GitHub Actions automation.
- LLM-backed incident synthesis.
- Hard release gates or review comments.

Context:
- ADRs:
  - `docs/adr/0013-adopt-phase1-postmortem-onboarding.md`
  - `docs/adr/0007-task-packets-are-the-verification-contract.md`
- Relevant files:
  - `src/coding_agent/__main__.py`
  - `src/coding_agent/postmortem_phase1.py`
  - `tests/coding_agent/test_postmortem_phase1.py`
  - `tests/cli/test_postmortem.py`

Target tests:
- `uv run pytest tests/coding_agent/test_postmortem_phase1.py tests/cli/test_postmortem.py -v`
- `uv run pytest tests/cli/ -v`

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
