Goal:
Split the oversized `coding_agent.__main__` CLI implementation into focused `coding_agent.cli` command modules without changing command behavior.

Scope:
- Keep `python -m coding_agent` and all existing Click command names/options working.
- Move command helper code from `src/coding_agent/__main__.py` into focused modules under `src/coding_agent/cli/`.
- Preserve compatibility exports for `create_agent` and `create_child_pipeline` from `coding_agent.__main__`.
- Keep this slice limited to CLI entrypoint structure.

Out of scope:
- Moving HTTP server/session manager files out of `ui/`.
- Moving Bee, Topic, runtime store, or observability modules.
- Changing command behavior, output formats, config precedence, or remote workflow semantics.

Context:
- ADRs:
  - `docs/adr/0048-application-structure-refactor-boundaries.md`
- Relevant files:
  - `src/coding_agent/__main__.py`
  - `src/coding_agent/cli/`
  - `tests/cli/`
  - `tests/coding_agent/test_bootstrap.py`
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`

Target tests:
- `uv run pytest tests/cli/ -v`
- `uv run pytest tests/coding_agent/test_bootstrap.py -v`

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
